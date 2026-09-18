"""API original-document download route.

BL-080 keeps object-store access inside the API trust boundary. The browser
webapp proxies this route instead of reading S3/MinIO directly.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import Any

import fitz
import httpx
from PIL import Image

import doc_ray.entrypoints.api.app as api_app
from doc_ray.config import Config
from doc_ray.entrypoints.api.app import create_app
from doc_ray.entrypoints.api.downloads import (
    _COVER_WIDTHS,
    _clamp_cover_width,
    _cover_cache_key,
    _render_pdf_cover_webp,
    _render_pdf_page_png,
)
from doc_ray.entrypoints.api.security import NoopAuthenticator

DOC_ID = "11111111-1111-1111-1111-111111111111"


class _Cursor:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self._row = row

    async def fetchone(self) -> dict[str, Any] | None:
        return self._row


class _Conn:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row
        self.queries: list[tuple[str, Any]] = []

    async def execute(self, query: str, params: Any = None) -> _Cursor:
        self.queries.append((query, params))
        if "FROM documents" in query:
            return _Cursor(self.row)
        return _Cursor()


class _ConnectionContext:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    async def __aenter__(self) -> _Conn:
        return self.conn

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _Pool:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def connection(self) -> _ConnectionContext:
        return _ConnectionContext(self.conn)


class _Store:
    bucket = "docray"

    def __init__(self, blobs: dict[str, bytes]) -> None:
        self.blobs = blobs
        self.keys: list[str] = []
        self.puts: list[tuple[str, bytes, str]] = []

    def get(self, key: str) -> bytes:
        self.keys.append(key)
        try:
            return self.blobs[key]
        except KeyError as exc:
            raise FileNotFoundError(key) from exc

    def put(self, key: str, data: bytes, content_type: str) -> str:
        self.puts.append((key, data, content_type))
        self.blobs[key] = data
        return f"s3://{self.bucket}/{key}"


def _cfg() -> Config:
    return replace(Config.from_env(), database_url="postgresql://unused/unused")


def _app(monkeypatch, row: dict[str, Any] | None, store: _Store):
    conn = _Conn(row)

    async def run_sync(func, *args):
        return func(*args)

    monkeypatch.setattr(api_app, "AsyncConnectionPool", lambda *_args, **_kwargs: _Pool(conn))
    monkeypatch.setattr(api_app, "ObjectStore", lambda _cfg: store)
    monkeypatch.setattr(api_app.anyio.to_thread, "run_sync", run_sync)
    app = create_app(_cfg(), authenticator=NoopAuthenticator())
    return app, conn


def _get(app, path: str) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path)

    return asyncio.run(request())


def _make_pdf(page_count: int = 2) -> bytes:
    doc = fitz.open()
    try:
        for index in range(page_count):
            page = doc.new_page(width=200, height=120)
            page.insert_text((24, 56), f"Page {index + 1}")
        return doc.tobytes()
    finally:
        doc.close()


def _pdf_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "source_uri": "s3://docray/documents/ab/report.pdf",
        "mime_type": "application/pdf",
        "original_filename": "Original Report.pdf",
        "title": "Original Report",
        "doc_type": "pdf",
        "status": "extracted",
        "content_sha256": "a" * 64,
        "page_count": 2,
    }
    row.update(overrides)
    return row


def test_document_download_serves_original_bytes_with_filename_and_mime_type(monkeypatch) -> None:
    body = b"%PDF-original"
    store = _Store({"documents/ab/report.pdf": body})
    app, conn = _app(
        monkeypatch,
        {
            "source_uri": "s3://docray/documents/ab/report.pdf",
            "mime_type": "application/pdf",
            "original_filename": "Original Report.pdf",
            "title": "Original Report",
            "doc_type": "pdf",
            "status": "extracted",
            "content_sha256": hashlib.sha256(body).hexdigest(),
        },
        store,
    )

    response = _get(app, f"/documents/{DOC_ID}/download")

    assert response.status_code == 200
    assert response.content == b"%PDF-original"
    assert response.headers["content-type"] == "application/pdf"
    assert 'filename="Original Report.pdf"' in response.headers["content-disposition"]
    assert store.keys == ["documents/ab/report.pdf"]
    assert any(params == (DOC_ID,) for _query, params in conn.queries)


def test_document_download_rejects_corrupt_original(monkeypatch) -> None:
    row = {
        "source_uri": "s3://docray/documents/ab/report.pdf",
        "mime_type": "application/pdf",
        "original_filename": "Original Report.pdf",
        "title": "Original Report",
        "doc_type": "pdf",
        "status": "extracted",
        "content_sha256": hashlib.sha256(b"expected").hexdigest(),
    }
    app, _conn = _app(monkeypatch, row, _Store({"documents/ab/report.pdf": b"corrupt"}))

    response = _get(app, f"/documents/{DOC_ID}/download")

    assert response.status_code == 409
    assert "sha256" in response.text


def test_document_download_returns_clean_errors_for_unavailable_original(monkeypatch) -> None:
    unavailable = {
        "source_uri": "s3://docray/documents/ab/report.pdf",
        "mime_type": "application/pdf",
        "original_filename": "Original Report.pdf",
        "title": "Original Report",
        "doc_type": "pdf",
        "status": "pending",
    }
    missing_blob = dict(unavailable, status="extracted")

    pending_app, _ = _app(monkeypatch, unavailable, _Store({"documents/ab/report.pdf": b"body"}))
    missing_app, _ = _app(monkeypatch, missing_blob, _Store({}))
    absent_app, _ = _app(monkeypatch, None, _Store({}))

    assert _get(pending_app, f"/documents/{DOC_ID}/download").status_code == 409
    assert _get(missing_app, f"/documents/{DOC_ID}/download").status_code == 404
    assert _get(absent_app, f"/documents/{DOC_ID}/download").status_code == 404


def test_pdf_page_render_serves_png_and_uses_object_store_cache(monkeypatch) -> None:
    pdf = _make_pdf(page_count=2)
    sha = hashlib.sha256(pdf).hexdigest()
    store = _Store({"documents/ab/report.pdf": pdf})
    app, _conn = _app(monkeypatch, _pdf_row(content_sha256=sha), store)
    cache_key = f"rendered-pages/{sha}/p1-dpi144.png"

    first = _get(app, f"/documents/{DOC_ID}/pages/1.png")

    assert first.status_code == 200
    assert first.content.startswith(b"\x89PNG")
    assert first.headers["content-type"] == "image/png"
    assert first.headers["x-docray-cache"] == "miss"
    assert store.keys == [cache_key, "documents/ab/report.pdf"]
    assert len(store.puts) == 1
    assert store.puts[0][0] == cache_key
    assert store.puts[0][2] == "image/png"

    second = _get(app, f"/documents/{DOC_ID}/pages/1.png")

    assert second.status_code == 200
    assert second.content == first.content
    assert second.headers["x-docray-cache"] == "hit"
    assert store.keys == [cache_key, "documents/ab/report.pdf", cache_key]
    assert len(store.puts) == 1


def test_pdf_page_render_rejects_corrupt_original(monkeypatch) -> None:
    pdf = _make_pdf(page_count=1)
    row = _pdf_row(content_sha256=hashlib.sha256(b"expected").hexdigest(), page_count=1)
    app, _conn = _app(monkeypatch, row, _Store({"documents/ab/report.pdf": pdf}))

    response = _get(app, f"/documents/{DOC_ID}/pages/1.png")

    assert response.status_code == 409
    assert "sha256" in response.text


def test_pdf_page_render_returns_clean_errors(monkeypatch) -> None:
    one_page_pdf = _make_pdf(page_count=1)
    non_pdf_app, _ = _app(
        monkeypatch,
        _pdf_row(mime_type="text/plain", doc_type="txt", page_count=1),
        _Store({"documents/ab/report.pdf": b"text"}),
    )
    out_of_range_app, _ = _app(
        monkeypatch,
        _pdf_row(page_count=1, content_sha256=hashlib.sha256(one_page_pdf).hexdigest()),
        _Store({"documents/ab/report.pdf": one_page_pdf}),
    )
    pending_app, _ = _app(
        monkeypatch,
        _pdf_row(status="pending"),
        _Store({"documents/ab/report.pdf": _make_pdf(page_count=1)}),
    )

    assert _get(non_pdf_app, f"/documents/{DOC_ID}/pages/1.png").status_code == 415
    assert _get(out_of_range_app, f"/documents/{DOC_ID}/pages/2.png").status_code == 404
    assert _get(out_of_range_app, f"/documents/{DOC_ID}/pages/0.png").status_code == 404
    assert _get(pending_app, f"/documents/{DOC_ID}/pages/1.png").status_code == 409


def test_a_failed_document_still_serves_its_stored_original(monkeypatch) -> None:
    """A failed *text* extraction says nothing about the bytes.

    Ingest stores the blob before extracting and keeps it on failure, yet all
    three asset routes gated on ``status == 'extracted'`` — so the file a
    reader most needs to inspect (the scan that could not be read) could
    neither be downloaded nor rendered. Only ``pending``, a row that exists
    before its bytes do, has nothing to serve.
    """
    pdf = _make_pdf(page_count=1)
    sha = hashlib.sha256(pdf).hexdigest()
    row = _pdf_row(status="failed", page_count=1, content_sha256=sha)

    download_app, _ = _app(monkeypatch, row, _Store({"documents/ab/report.pdf": pdf}))
    page_app, _ = _app(monkeypatch, row, _Store({"documents/ab/report.pdf": pdf}))
    cover_app, _ = _app(monkeypatch, row, _Store({"documents/ab/report.pdf": pdf}))

    assert _get(download_app, f"/documents/{DOC_ID}/download").status_code == 200
    assert _get(page_app, f"/documents/{DOC_ID}/pages/1.png").status_code == 200
    assert _get(cover_app, f"/documents/{DOC_ID}/cover.webp").status_code == 200


# --- Cover delivery: small WebP thumbnails (docs/design/cover-delivery.md) -----------

_FIXTURE_PDF = Path(__file__).resolve().parents[3] / "static" / "fixtures" / "AirFrance447_BEA.pdf"


def test_clamp_cover_width_snaps_to_the_allowlist() -> None:
    assert set(_COVER_WIDTHS) == {160, 320, 640}
    assert _clamp_cover_width(320) == 320
    assert _clamp_cover_width(300) == 320  # nearest allowed
    assert _clamp_cover_width(5) == 160  # clamp below the floor
    assert _clamp_cover_width(100_000) == 640  # clamp above the ceiling


def test_cover_cache_key_is_versioned_width_and_webp_scoped() -> None:
    row = {"content_sha256": "deadbeefcafe"}
    key = _cover_cache_key(DOC_ID, row, 320)
    assert key == "rendered-pages/deadbeefcafe/cover-p1-w320.webp"
    assert _cover_cache_key(DOC_ID, row, 160) != key  # no cross-size cache collision
    assert f"rendered-pages/{DOC_ID}/" in _cover_cache_key(DOC_ID, {}, 320)  # falls back to doc id


def test_render_pdf_cover_webp_is_a_small_webp_at_the_target_width() -> None:
    pdf = _FIXTURE_PDF.read_bytes()
    webp = _render_pdf_cover_webp(pdf, 1, 320)
    assert webp[:4] == b"RIFF" and webp[8:12] == b"WEBP"  # WebP container magic
    assert Image.open(BytesIO(webp)).width == 320
    # The whole point: far smaller than the 144-DPI full-page PNG, and tiny.
    assert len(webp) < len(_render_pdf_page_png(pdf, 1))
    # Byte-budget guard: a regression that re-serves the full page blows this.
    assert len(webp) < 60_000


def test_cover_route_serves_small_webp_and_uses_the_object_store_cache(monkeypatch) -> None:
    pdf = _make_pdf(page_count=2)
    sha = hashlib.sha256(pdf).hexdigest()
    store = _Store({"documents/ab/report.pdf": pdf})
    app, _conn = _app(monkeypatch, _pdf_row(content_sha256=sha), store)
    cover_key = f"rendered-pages/{sha}/cover-p1-w320.webp"

    first = _get(app, f"/documents/{DOC_ID}/cover.webp?w=320")
    assert first.status_code == 200
    assert first.headers["content-type"] == "image/webp"
    assert first.headers["x-docray-cache"] == "miss"
    assert "immutable" in first.headers["cache-control"]
    assert first.content[:4] == b"RIFF"
    assert store.puts[0][0] == cover_key
    assert store.puts[0][2] == "image/webp"

    second = _get(app, f"/documents/{DOC_ID}/cover.webp?w=320")
    assert second.headers["x-docray-cache"] == "hit"
    assert second.content == first.content


def test_cover_route_clamps_an_arbitrary_width_to_the_allowlist(monkeypatch) -> None:
    pdf = _make_pdf(page_count=1)
    store = _Store({"documents/ab/report.pdf": pdf})
    row = _pdf_row(page_count=1, content_sha256=hashlib.sha256(pdf).hexdigest())
    app, _conn = _app(monkeypatch, row, store)
    _get(app, f"/documents/{DOC_ID}/cover.webp?w=999")  # 999 -> nearest allowed = 640
    assert any(key.endswith("cover-p1-w640.webp") for key, *_ in store.puts)


def test_cover_route_is_404_when_the_document_is_not_readable(monkeypatch) -> None:
    # RLS filters the row to None for a caller without access -> 404, never a leak.
    app, _conn = _app(monkeypatch, None, _Store({}))
    assert _get(app, f"/documents/{DOC_ID}/cover.webp").status_code == 404
