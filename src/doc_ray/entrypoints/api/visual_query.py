"""GraphQL ``VisualQueries`` mixin — the Prism visual-layer read fields.

Split out of the ``Query`` root (D-015), same pattern as ``BillingMutations``:
``Query`` inherits this mixin so the visual resolvers live in their own module.
Every field clamps its page size and delegates to the async visual read adapter,
RLS-scoped like every other read.
"""

from __future__ import annotations

import functools
import os

import anyio
import strawberry

from doc_ray.adapters import persistence_visual_read as visual_read
from doc_ray.entrypoints.api.ids import clean_uuid
from doc_ray.entrypoints.api.types import Info
from doc_ray.entrypoints.api.visual_types import (
    CrossModalImageHit,
    DocumentImage,
    ImageClassCount,
    VisualCaption,
)
from doc_ray.ports.vision_encoder import VisionEncoder


@functools.lru_cache(maxsize=1)
def _query_vision_encoder() -> VisionEncoder:
    """Lazily build the (cached) cross-modal query encoder from env config.

    Loads the vision model on first cross-modal query only — the model is required
    only where cross-modal search runs. Isolated here so the schema builds without
    importing torch/open_clip.
    """
    from doc_ray.adapters.vision_encoder_openclip import OpenClipVisionEncoder

    return OpenClipVisionEncoder(
        model_name=os.environ.get("DOCRAY_VISION_MODEL", "ViT-L-14"),
        pretrained=os.environ.get("DOCRAY_VISION_PRETRAINED", "openai"),
    )


@strawberry.type
class VisualQueries:
    """Prism visual-layer read fields, mixed into the GraphQL ``Query`` root."""

    @strawberry.field
    async def document_images(
        self,
        info: Info,
        document_id: strawberry.ID,
        limit: int = 100,
        after: str | None = None,
    ) -> list[DocumentImage]:
        """Return a document's detected images + Tier-1 visual features (RLS-scoped)."""
        doc_id = clean_uuid(str(document_id))
        if doc_id is None:
            return []
        ctx = info.context
        rows = await visual_read.list_document_images(
            ctx.conn, doc_id, limit=ctx.page_size(limit), after=after
        )
        return [DocumentImage.from_row(r) for r in rows]

    @strawberry.field
    async def image_class_summary(
        self, info: Info, document_id: strawberry.ID
    ) -> list[ImageClassCount]:
        """Return the ``(image_class, count)`` distribution for a document's images."""
        doc_id = clean_uuid(str(document_id))
        if doc_id is None:
            return []
        rows = await visual_read.image_class_summary(info.context.conn, doc_id)
        return [ImageClassCount.from_row(r) for r in rows]

    @strawberry.field
    async def visual_captions(
        self, info: Info, document_id: strawberry.ID, limit: int = 100
    ) -> list[VisualCaption]:
        """Return a document's COMMITTED, grounded Tier-3 captions (RLS-scoped).

        Only trusted (``status='committed'``) captions are exposed; low-confidence
        captions stay in the ``visual_review`` queue and are never returned here.
        """
        doc_id = clean_uuid(str(document_id))
        if doc_id is None:
            return []
        ctx = info.context
        rows = await visual_read.list_visual_captions(ctx.conn, doc_id, limit=ctx.page_size(limit))
        return [VisualCaption.from_row(r) for r in rows]

    @strawberry.field
    async def cross_modal_image_search(
        self, info: Info, query: str, limit: int = 25
    ) -> list[CrossModalImageHit]:
        """Rank images by cross-modal similarity to *query* (text->image), RLS-scoped.

        Embeds the query through the vision model's text tower (loaded lazily on
        the API host) and ranks ``image_embedding`` by HNSW cosine distance.
        """
        from doc_ray.application.visual_embed import to_vector_literal

        ctx = info.context
        encoder = _query_vision_encoder()
        vectors = await anyio.to_thread.run_sync(encoder.embed_texts, [query])
        query_vector = to_vector_literal(vectors[0])
        rows = await visual_read.cross_modal_image_search(
            ctx.conn, query_vector, encoder.model_id, limit=ctx.page_size(limit)
        )
        return [CrossModalImageHit.from_row(r) for r in rows]
