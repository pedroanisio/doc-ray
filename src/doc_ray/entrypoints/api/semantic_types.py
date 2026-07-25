"""GraphQL type for semantic sentence retrieval (NLP Layer 20)."""

from __future__ import annotations

from typing import Any

import strawberry


@strawberry.type
class SemanticSentenceHit:
    """A sentence ranked by learned (embedding) similarity to a query.

    ``similarity`` is ``1 - cosine_distance`` in ``(-1, 1]``; higher is closer.
    Ranking quality depends on the active embedding model, which is server-side.
    """

    sentence_id: strawberry.ID
    document_id: strawberry.ID
    ordinal: int
    text: str
    similarity: float

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> SemanticSentenceHit:
        """Map a ``persistence_semantic_read.semantic_search_sentences`` row."""
        return cls(
            sentence_id=strawberry.ID(str(row["sentence_id"])),
            document_id=strawberry.ID(str(row["document_id"])),
            ordinal=int(row["ordinal"]),
            text=str(row["text"]),
            similarity=float(row["similarity"]),
        )
