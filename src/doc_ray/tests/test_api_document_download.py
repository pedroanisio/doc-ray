"""API original-document download route.

BL-080 keeps object-store access inside the API trust boundary. The browser
webapp proxies this route instead of reading S3/MinIO directly.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from typing import Any

import fitz
import httpx

import doc_ray.entrypoints.api.app as api_app
from doc_ray.config import Config
from doc_ray.entrypoints.api.app import create_app
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
