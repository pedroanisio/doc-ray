"""RLS and thumbnail regressions for extracted document-image delivery."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from io import BytesIO
from typing import Any

import httpx
from PIL import Image

import doc_ray.entrypoints.api.app as api_app
from doc_ray.config import Config
from doc_ray.entrypoints.api.app import create_app
from doc_ray.entrypoints.api.security import NoopAuthenticator

DOC_ID = "11111111-1111-1111-1111-111111111111"
IMAGE_ID = "01J00000000000000000000000"


class _Cursor:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.row = row

    async def fetchone(self):
        return self.row


class _Conn:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row
        self.queries: list[tuple[str, Any]] = []

    async def execute(self, query: str, params: Any = None):
        self.queries.append((query, params))
        return _Cursor(self.row if "FROM document_image" in query else None)


class _ConnectionContext:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_exc):
        return None


class _Pool:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    async def open(self):
        return None

    async def close(self):
        return None

    def connection(self):
        return _ConnectionContext(self.conn)


class _Store:
    bucket = "docray"

    def __init__(self, blobs: dict[str, bytes]) -> None:
        self.blobs = blobs
        self.keys: list[str] = []
        self.puts: list[tuple[str, bytes, str]] = []

    def get(self, key: str) -> bytes:
        self.keys.append(key)
        if key not in self.blobs:
            raise FileNotFoundError(key)
        return self.blobs[key]

    def put(self, key: str, data: bytes, content_type: str) -> str:
        self.puts.append((key, data, content_type))
        self.blobs[key] = data
        return f"s3://docray/{key}"


def _png(width: int = 1200, height: int = 800) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), "#cc3344").save(buffer, "PNG")
    return buffer.getvalue()


def _app(monkeypatch, row: dict[str, Any] | None, store: _Store):
    conn = _Conn(row)

    async def run_sync(func, *args):
        return func(*args)

    monkeypatch.setattr(api_app, "AsyncConnectionPool", lambda *_a, **_k: _Pool(conn))
    monkeypatch.setattr(api_app, "ObjectStore", lambda _cfg: store)
    monkeypatch.setattr("doc_ray.entrypoints.api.visual_assets.anyio.to_thread.run_sync", run_sync)
    cfg = replace(Config.from_env(), database_url="postgresql://unused/unused")
    return create_app(cfg, authenticator=NoopAuthenticator()), conn


def _get(app, path: str) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path)

    return asyncio.run(request())


def test_document_image_serves_bounded_webp_after_rls_lookup(monkeypatch) -> None:
    source = _png()
    key = "document-images/doc/image.png"
    store = _Store({key: source})
    row = {"object_key": key, "sha256": hashlib.sha256(source).hexdigest()}
    app, conn = _app(monkeypatch, row, store)

    response = _get(app, f"/documents/{DOC_ID}/images/{IMAGE_ID}?w=320")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    assert "immutable" in response.headers["cache-control"]
    assert Image.open(BytesIO(response.content)).width == 320
    query, params = next(item for item in conn.queries if "FROM document_image" in item[0])
    assert "JOIN documents" in query
    assert params == (IMAGE_ID, DOC_ID)
    assert store.puts[0][2] == "image/webp"


def test_document_image_is_404_when_rls_hides_or_id_is_unknown(monkeypatch) -> None:
    store = _Store({"document-images/doc/image.png": _png()})
    app, _conn = _app(monkeypatch, None, store)

    response = _get(app, f"/documents/{DOC_ID}/images/{IMAGE_ID}")

    assert response.status_code == 404
    assert store.keys == []


def test_document_image_missing_object_returns_clean_404(monkeypatch) -> None:
    row = {"object_key": "document-images/doc/missing.png", "sha256": "a" * 64}
    app, _conn = _app(monkeypatch, row, _Store({}))

    assert _get(app, f"/documents/{DOC_ID}/images/{IMAGE_ID}").status_code == 404
