"""GraphQL types for durable NER corrections and exact-surface previews."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

import strawberry

Row = dict[str, Any]


@strawberry.type
class EntityMentionCorrection:
    """One durable human correction to an ML-produced NER mention.

    Span actions carry ``charStart``/``charEnd``. Surface actions honestly leave
    both offsets null and expose their exact ``surface`` +
    ``surfaceOriginalType`` matcher. The append-only order is the replay order,
    so later narrow exceptions can supersede earlier systematic rules.
    """

    id: strawberry.ID
    document_id: strawberry.ID
    action: str
    char_start: int | None
    char_end: int | None
    entity_type: str | None
    surface: str | None
    new_char_start: int | None
    new_char_end: int | None
    surface_original_type: str | None
    original_type: str | None
    rationale: str | None
    created_at: datetime

    @classmethod
    def from_row(cls, row: Row) -> EntityMentionCorrection:
        """Build an audit entry from an ``entity_mention_correction`` row."""
        return cls(
            id=strawberry.ID(str(row["id"])),
            document_id=strawberry.ID(str(row["document_id"])),
            action=str(row["action"]),
            char_start=row.get("char_start"),
            char_end=row.get("char_end"),
            entity_type=row.get("entity_type"),
            surface=row.get("surface"),
            new_char_start=row.get("new_char_start"),
            new_char_end=row.get("new_char_end"),
            surface_original_type=row.get("surface_original_type"),
            original_type=row.get("original_type"),
            rationale=row.get("rationale"),
            created_at=cast(datetime, row["created_at"]),
        )


@strawberry.type
class EntitySurfaceSample:
    """One bounded example of an exact surface/type occurrence."""

    mention_id: strawberry.ID
    char_start: int
    char_end: int
    sentence_ordinal: int | None
    sentence_context: str | None

    @classmethod
    def from_row(cls, row: Row) -> EntitySurfaceSample:
        """Map one preview sample row."""
        return cls(
            mention_id=strawberry.ID(str(row["mention_id"])),
            char_start=int(row["char_start"]),
            char_end=int(row["char_end"]),
            sentence_ordinal=(
                int(row["sentence_ordinal"]) if row.get("sentence_ordinal") is not None else None
            ),
            sentence_context=(
                str(row["sentence_context"]) if row.get("sentence_context") is not None else None
            ),
        )


@strawberry.type
class EntitySurfaceOccurrence:
    """Per-document impact preview for one exact surface/current-type rule.

    ``occurrenceCount`` never exceeds ``countCap``. When ``countCapped`` is true,
    the document has at least one additional match; the capped number must not be
    presented as the exact total.
    """

    document_id: strawberry.ID
    surface: str
    original_type: str
    occurrence_count: int
    count_cap: int
    count_capped: bool
    samples: list[EntitySurfaceSample]

    @classmethod
    def from_row(cls, row: Row) -> EntitySurfaceOccurrence:
        """Map one per-document preview result."""
        return cls(
            document_id=strawberry.ID(str(row["document_id"])),
            surface=str(row["surface"]),
            original_type=str(row["original_type"]),
            occurrence_count=int(row["occurrence_count"]),
            count_cap=int(row["count_cap"]),
            count_capped=bool(row["count_capped"]),
            samples=[EntitySurfaceSample.from_row(sample) for sample in row["samples"]],
        )
