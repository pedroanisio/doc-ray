"""GraphQL review queue for materialized mention-confidence ranks (Block G)."""

from __future__ import annotations

from typing import Any

import strawberry

from doc_ray.adapters import persistence_mention_confidence_read as confidence_read
from doc_ray.entrypoints.api.ids import clean_uuid
from doc_ray.entrypoints.api.types import Info


@strawberry.type
class UncertainMention:
    """One uncorrected mention ranked for review, with its heuristic reasons.

    ``confidence`` is a triage rank, not a calibrated model probability. Lower
    values should be reviewed first; ``reasons`` exposes the deterministic
    signals that produced the rank.
    """

    id: strawberry.ID
    document_id: strawberry.ID
    char_start: int
    char_end: int
    surface: str
    entity_type: str
    confidence: float
    reasons: list[str]
    method: str
    cursor: str

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> UncertainMention:
        """Map a materialized mention-confidence reader row."""
        return cls(
            id=strawberry.ID(str(row["id"])),
            document_id=strawberry.ID(str(row["document_id"])),
            char_start=int(row["char_start"]),
            char_end=int(row["char_end"]),
            surface=str(row["surface"]),
            entity_type=str(row["entity_type"]),
            confidence=float(row["confidence"]),
            reasons=[str(reason) for reason in row["confidence_reasons"]],
            method=str(row["confidence_method"]),
            cursor=str(row["cursor"]),
        )


@strawberry.type
class MentionConfidenceQueries:
    """Read fields for human review of uncertain extracted mentions."""

    @strawberry.field
    async def uncertain_mentions(
        self,
        info: Info,
        document_id: strawberry.ID,
        limit: int = 100,
        entity_type: str | None = None,
        after: str | None = None,
    ) -> list[UncertainMention]:
        """Return uncorrected mentions in ascending confidence order.

        Values and explanations are materialized by ``score_mentions``; this
        bounded read performs no live corpus aggregation. Page with the last
        row's opaque ``cursor``.
        """
        doc_id = clean_uuid(str(document_id))
        if doc_id is None:
            return []
        ctx = info.context
        rows = await confidence_read.uncertain_mentions(
            ctx.conn,
            doc_id,
            entity_type=entity_type,
            limit=ctx.page_size(limit),
            after=after,
        )
        return [UncertainMention.from_row(row) for row in rows]
