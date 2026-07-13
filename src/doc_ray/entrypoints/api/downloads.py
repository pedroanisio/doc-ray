"""Object-store streaming routes: document originals, PDF page PNGs, backups.

Split out of :mod:`doc_ray.entrypoints.api.app` (D-015), following the
``register_metrics_route``/``register_saml_routes`` sibling precedent. Every
route authenticates the caller and binds the principal to its pooled
connection so RLS is exercised (BL-094); blocking object-store calls run off
the event loop via ``anyio.to_thread``.
"""

from __future__ import annotations

from typing import Any, cast
from urllib.parse import urlparse

import anyio
import psycopg
from botocore.exceptions import ClientError
from fastapi import FastAPI, HTTPException, Request, Response
from psycopg_pool import AsyncConnectionPool

from doc_ray.adapters.object_store import ObjectStore
from doc_ray.application.integrity import IntegrityError, verify_bytes
from doc_ray.entrypoints.api.security import (
    Authenticator,
    apply_principal,
    reset_principal,
)

_PDF_PAGE_RENDER_DPI = 144


def _object_key_from_source_uri(source_uri: str, bucket: str) -> str | None:
    """Return the object-store key for a canonical ``s3://bucket/key`` URI."""
    parsed = urlparse(source_uri)
    if parsed.scheme != "s3" or parsed.netloc != bucket:
        return None
    key = parsed.path.lstrip("/")
    return key or None


def _attachment_filename(row: dict[str, Any], document_id: str) -> str:
    """Choose a safe download filename from document metadata."""
    fallback_ext = str(row.get("doc_type") or "bin").lstrip(".") or "bin"
    fallback = f"{row.get('title') or document_id}.{fallback_ext}"
    filename = str(row.get("original_filename") or fallback)
    return filename.replace("\r", "").replace("\n", "").replace('"', "") or f"{document_id}.bin"


def _is_missing_object(exc: ClientError) -> bool:
    """Return true when an S3-style exception means the object is absent."""
    code = str(exc.response.get("Error", {}).get("Code", ""))
    return code in {"NoSuchKey", "NoSuchBucket", "404", "NotFound"}


def _is_pdf_document(row: dict[str, Any]) -> bool:
    """Return true when document metadata describes an original PDF."""
    return row.get("doc_type") == "pdf" or row.get("mime_type") == "application/pdf"


def _pdf_page_cache_key(document_id: str, row: dict[str, Any], page_number: int) -> str:
    """Return the stable object-store key for a rendered PDF page image."""
    version = str(row.get("content_sha256") or document_id)
    return f"rendered-pages/{version}/p{page_number}-dpi{_PDF_PAGE_RENDER_DPI}.png"


def _render_pdf_page_png(pdf_bytes: bytes, page_number: int) -> bytes:
    """Render one 1-based PDF page to PNG bytes."""
    import fitz

    if page_number < 1:
        raise IndexError(page_number)
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        if page_number > doc.page_count:
            raise IndexError(page_number)
        page = doc.load_page(page_number - 1)
        pixmap = page.get_pixmap(dpi=_PDF_PAGE_RENDER_DPI, alpha=False)
        return cast(bytes, pixmap.tobytes("png"))


async def _get_cached_object(store: ObjectStore, key: str) -> bytes | None:
    """Return cached object bytes, or None when the key is absent."""
    try:
        return await anyio.to_thread.run_sync(store.get, key)
    except FileNotFoundError:
        return None
    except ClientError as exc:
        if _is_missing_object(exc):
            return None
        raise


async def _get_required_object(
    store: ObjectStore,
    key: str,
    detail: str,
    *,
    expected_sha256: str | None = None,
    context: str | None = None,
) -> bytes:
    """Return object bytes, translating object-store misses into HTTP 404."""
    try:
        data = await anyio.to_thread.run_sync(store.get, key)
        return verify_bytes(data, expected_sha256, context=context or key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=detail) from exc
    except ClientError as exc:
        if _is_missing_object(exc):
            raise HTTPException(status_code=404, detail=detail) from exc
        raise
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def _document_download_response(document_id: str, request: Request) -> Response:
    """Build the original-document download response for the API route."""
    authenticator: Authenticator = request.app.state.authenticator
    principal = await authenticator.authenticate(request)
    pool: AsyncConnectionPool = request.app.state.pool
    store: ObjectStore = request.app.state.object_store
    async with pool.connection() as raw_conn:
        conn = cast(psycopg.AsyncConnection[dict[str, Any]], raw_conn)
        await apply_principal(conn, principal)
        try:
            cur = await conn.execute(
                "SELECT source_uri, mime_type, original_filename, title, doc_type, status, "
                "content_sha256 "
                "FROM documents WHERE id = %s",
                (document_id,),
            )
            row = await cur.fetchone()
        finally:
            await reset_principal(conn, principal)

    if row is None:
        raise HTTPException(status_code=404, detail="document not found")
    if row.get("status") != "extracted":
        raise HTTPException(status_code=409, detail="document original is not available yet")

    key = _object_key_from_source_uri(str(row.get("source_uri") or ""), store.bucket)
    if key is None:
        raise HTTPException(status_code=404, detail="document original is missing")
    data = await _get_required_object(
        store,
        key,
        "document original is missing",
        expected_sha256=row.get("content_sha256"),
        context=f"document {document_id} original",
    )

    filename = _attachment_filename(row, document_id)
    return Response(
        content=data,
        media_type=row.get("mime_type") or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _document_page_png_response(
    document_id: str, page_number: int, request: Request
) -> Response:
    """Build a cached original-PDF page PNG response for the API route."""
    if page_number < 1:
        raise HTTPException(status_code=404, detail="page not found")

    authenticator: Authenticator = request.app.state.authenticator
    principal = await authenticator.authenticate(request)
    pool: AsyncConnectionPool = request.app.state.pool
    store: ObjectStore = request.app.state.object_store
    async with pool.connection() as raw_conn:
        conn = cast(psycopg.AsyncConnection[dict[str, Any]], raw_conn)
        await apply_principal(conn, principal)
        try:
            cur = await conn.execute(
                "SELECT source_uri, mime_type, doc_type, status, content_sha256, page_count "
                "FROM documents WHERE id = %s",
                (document_id,),
            )
            row = await cur.fetchone()
        finally:
            await reset_principal(conn, principal)

    if row is None:
        raise HTTPException(status_code=404, detail="document not found")
    if row.get("status") != "extracted":
        raise HTTPException(status_code=409, detail="document original is not available yet")
    if not _is_pdf_document(row):
        raise HTTPException(status_code=415, detail="document is not a PDF")

    page_count = row.get("page_count")
    if page_count is not None and page_number > int(page_count):
        raise HTTPException(status_code=404, detail="page not found")

    cache_key = _pdf_page_cache_key(document_id, row, page_number)
    cached = await _get_cached_object(store, cache_key)
    if cached is not None:
        return Response(
            content=cached,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=86400", "X-DocRay-Cache": "hit"},
        )

    key = _object_key_from_source_uri(str(row.get("source_uri") or ""), store.bucket)
    if key is None:
        raise HTTPException(status_code=404, detail="document original is missing")
    pdf_bytes = await _get_required_object(
        store,
        key,
        "document original is missing",
        expected_sha256=row.get("content_sha256"),
        context=f"document {document_id} original",
    )

    try:
        png = await anyio.to_thread.run_sync(_render_pdf_page_png, pdf_bytes, page_number)
    except (IndexError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="page not found") from exc
    await anyio.to_thread.run_sync(store.put, cache_key, png, "image/png")
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400", "X-DocRay-Cache": "miss"},
    )


def register_download_routes(app: FastAPI) -> None:
    """Attach the document/backup streaming routes to *app*."""

    @app.get("/backups/{backup_id}/download")
    async def download_backup(backup_id: str, request: Request) -> Response:
        """Stream a catalogued backup archive from the object store.

        The object store sits inside the API's network/trust boundary, so the
        browser-facing webapp proxies downloads through here rather than reaching
        the store directly — a host-side ``S3_ENDPOINT`` mismatch can never break
        the download. Backups are corpus-wide and admin-only, the same as
        createBackup/restoreBackup (BL-064/D-019): this route authenticates the
        caller and requires ``docray_is_admin()`` through a principal-bound
        connection (``apply_principal``), so RLS is actually exercised rather
        than bypassed via the raw superuser pool (BL-094). Only ``available``
        archives are served; anything else is a 404. The blocking ``get`` runs
        off the event loop.
        """
        authenticator: Authenticator = request.app.state.authenticator
        principal = await authenticator.authenticate(request)
        if principal.principal_id is None:
            raise HTTPException(status_code=401, detail="authentication required")

        pool: AsyncConnectionPool = request.app.state.pool
        store: ObjectStore = request.app.state.object_store
        async with pool.connection() as raw_conn:
            conn = cast(psycopg.AsyncConnection[dict[str, Any]], raw_conn)
            await apply_principal(conn, principal)
            try:
                admin_cur = await conn.execute("SELECT docray_is_admin() AS ok")
                admin_row = await admin_cur.fetchone()
                if not admin_row or not admin_row["ok"]:
                    raise HTTPException(status_code=403, detail="admin principal required")
                cur = await conn.execute(
                    "SELECT object_key FROM backups WHERE id = %s AND status = 'available'",
                    (backup_id,),
                )
                row = await cur.fetchone()
            finally:
                await reset_principal(conn, principal)
        if row is None or not row["object_key"]:
            return Response(status_code=404)
        data = await anyio.to_thread.run_sync(store.get, str(row["object_key"]))
        return Response(
            content=data,
            media_type="application/gzip",
            headers={"Content-Disposition": f'attachment; filename="{backup_id}.tar.gz"'},
        )

    @app.get("/documents/{document_id}/download")
    async def download_document(document_id: str, request: Request) -> Response:
        """Stream the original uploaded document bytes from the object store.

        The API owns S3/MinIO access. Browser callers use the webapp proxy, which
        forwards the same read credential it uses for backup downloads. The row
        lookup runs on a principal-bound connection so document RLS still decides
        which originals are readable.
        """
        return await _document_download_response(document_id, request)

    @app.get("/documents/{document_id}/pages/{page_number}.png")
    async def render_document_page(
        document_id: str, page_number: int, request: Request
    ) -> Response:
        """Render a cached PNG image for one original-PDF page."""
        return await _document_page_png_response(document_id, page_number, request)
