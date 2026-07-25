"""GraphQL ``SemanticQueries`` mixin — the learned sentence-retrieval read field.

Split out of the ``Query`` root (D-015), same pattern as ``VisualQueries``:
``Query`` inherits this mixin so the semantic resolver lives in its own module.
The field clamps its page size and delegates to the async semantic read adapter,
RLS-scoped like every other read.

Embedding the query requires the sentence-transformers stack on the API host
(loaded lazily on first semantic query, exactly like the vision encoder). On a
host without it the field fails typed-and-actionable
(:class:`SemanticStackUnavailableError`) rather than leaking a raw
``ModuleNotFoundError`` through GraphQL.
"""

from __future__ import annotations

import functools

import anyio
import strawberry

from doc_ray.adapters import persistence_semantic_read as semantic_read
from doc_ray.adapters.text_encoder_sentence_transformers import build_text_encoder
from doc_ray.entrypoints.api.ids import clean_uuid
from doc_ray.entrypoints.api.semantic_types import SemanticSentenceHit
from doc_ray.entrypoints.api.types import Info
from doc_ray.ports.text_encoder import TextEncoder


@functools.lru_cache(maxsize=1)
def _query_text_encoder() -> TextEncoder:
    """Lazily build the (cached) query encoder from env config.

    Raises:
        SemanticStackUnavailableError: If the sentence-transformers stack is not
            installed on this host — the message keeps the stable ``"not
            installed"`` marker the MCP layer classifies on.
    """
    return build_text_encoder()


@strawberry.type
class SemanticQueries:
    """Learned semantic-retrieval read field, mixed into the GraphQL ``Query`` root."""

    @strawberry.field
    async def semantic_search_sentences(
        self,
        info: Info,
        query: str,
        document_id: strawberry.ID | None = None,
        limit: int = 25,
    ) -> list[SemanticSentenceHit]:
        """Rank sentences by learned semantic similarity to *query*, RLS-scoped.

        Embeds *query* through the production sentence encoder (loaded lazily on
        the API host) and ranks the ``embedding`` table by HNSW cosine distance.
        Unlike lexical search it does not flood on a common term. Requires the
        sentence-transformers stack; without it the call fails typed.
        """
        from doc_ray.application.semantic_embed import to_vector_literal

        ctx = info.context
        doc_id: str | None = None
        if document_id is not None:
            doc_id = clean_uuid(str(document_id))
            if doc_id is None:
                return []
        encoder = _query_text_encoder()
        vectors = await anyio.to_thread.run_sync(encoder.embed_texts, [query])
        query_vector = to_vector_literal(vectors[0])
        rows = await semantic_read.semantic_search_sentences(
            ctx.conn,
            query_vector,
            encoder.model_id,
            document_id=doc_id,
            limit=ctx.page_size(limit),
        )
        return [SemanticSentenceHit.from_row(r) for r in rows]
