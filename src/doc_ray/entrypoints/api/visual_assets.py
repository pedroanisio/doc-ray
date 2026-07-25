"""RLS-gated extracted-image thumbnail delivery.

The object key is never accepted from the caller. A principal-bound database
lookup joins the requested image to its readable document, then the API fetches
and verifies the registered object before producing a bounded WebP thumbnail.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any, cast

import anyio
import psycopg
from fastapi import FastAPI, HTTPException, Request, Response
from PIL import Image, UnidentifiedImageError
from psycopg_pool import AsyncConnectionPool

from doc_ray.adapters.object_store import ObjectStore
from doc_ray.entrypoints.api.downloads import (
    _clamp_cover_width,
    _get_cached_object,
    _get_required_object,
)
from doc_ray.entrypoints.api.security import Authenticator, apply_principal, reset_principal

_CACHE_CONTROL = "private, max-age=31536000, immutable"


def _thumbnail_webp(data: bytes, width: int) -> bytes:
    """Decode image *data* and return a bounded, aspect-preserving WebP."""
    try:
        with Image.open(BytesIO(data)) as source:
            source.load()
            image = source.convert("RGBA" if source.mode in {"RGBA", "LA"} else "RGB")
            if image.width > width:
                height = max(1, round(image.height * width / image.width))
                image = image.resize((width, height), Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, "WEBP", quality=82, method=6)
            return output.getvalue()
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError("registered document image is not decodable") from exc


async def _document_image_response(
    document_id: str, image_id: str, width: int, request: Request
) -> Response:
    """Build a thumbnail response after a principal-bound image/document lookup."""
    authenticator: Authenticator = request.app.state.authenticator
    principal = await authenticator.authenticate(request)
    pool: AsyncConnectionPool = request.app.state.pool
    store: ObjectStore = request.app.state.object_store
    async with pool.connection() as raw_conn:
        conn = cast(psycopg.AsyncConnection[dict[str, Any]], raw_conn)
        await apply_principal(conn, principal)
        try:
            cur = await conn.execute(
                """
                SELECT di.object_key, di.sha256
                FROM document_image di
                JOIN documents d ON d.id = di.document_id
                WHERE di.id = %s AND di.document_id = %s
                """,
                (image_id, document_id),
            )
            row = await cur.fetchone()
        finally:
            await reset_principal(conn, principal)
    if row is None:
        raise HTTPException(status_code=404, detail="document image not found")

    sha = str(row["sha256"])
    cache_key = f"visual-thumbnails/{sha}/w{width}.webp"
    cached = await _get_cached_object(store, cache_key)
    cache_state = "hit"
    if cached is None:
        source = await _get_required_object(
            store,
            str(row["object_key"]),
            "document image is missing",
            expected_sha256=sha,
            context=f"document image {image_id}",
        )
        try:
            cached = await anyio.to_thread.run_sync(_thumbnail_webp, source, width)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await anyio.to_thread.run_sync(store.put, cache_key, cached, "image/webp")
        cache_state = "miss"
    return Response(
        content=cached,
        media_type="image/webp",
        headers={"Cache-Control": _CACHE_CONTROL, "X-DocRay-Cache": cache_state},
    )


def register_visual_asset_routes(app: FastAPI) -> None:
    """Attach the extracted document-image thumbnail route."""

    @app.get("/documents/{document_id}/images/{image_id}")
    async def document_image(
        document_id: str, image_id: str, request: Request, w: int = 320
    ) -> Response:
        """Serve one bounded WebP thumbnail if the caller may read its document."""
        return await _document_image_response(document_id, image_id, _clamp_cover_width(w), request)
