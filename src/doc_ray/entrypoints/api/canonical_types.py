"""GraphQL types for canonical registry and review surfaces."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

import strawberry
from strawberry.scalars import JSON

Row = dict[str, Any]


@strawberry.type
class CanonicalForm:
    """One surface form registered for a canonical entity."""

    form_norm: str
    form_surface: str
    lang: str
    form_type: str
    confidence: float
    occurrences: int
    source_types: list[str]

    @classmethod
    def from_row(cls, row: Row) -> CanonicalForm:
        """Build a ``CanonicalForm`` from a read-adapter row."""
        return cls(
            form_norm=row["form_norm"],
            form_surface=row["form_surface"],
            lang=row["lang"],
            form_type=row["form_type"],
            confidence=float(row["confidence"]),
            occurrences=row["occurrences"],
            source_types=list(row.get("source_types") or []),
        )


@strawberry.type
class CanonicalEntity:
    """One canonical registry entity."""

    id: strawberry.ID
    preferred: str
    entity_type: str
    wikidata_id: str | None
    description: str | None
    lang_primary: str | None
    forms: list[CanonicalForm]
    cursor: str

    @classmethod
    def from_row(cls, row: Row) -> CanonicalEntity:
        """Build a ``CanonicalEntity`` from a read-adapter row."""
        forms = cast(list[Row], row.get("forms") or [])
        return cls(
            id=strawberry.ID(str(row["id"])),
            preferred=row["preferred"],
            entity_type=row["entity_type"],
            wikidata_id=row.get("wikidata_id"),
            description=row.get("description"),
            lang_primary=row.get("lang_primary"),
            forms=[CanonicalForm.from_row(form) for form in forms],
            cursor=row.get("cursor") or str(row["id"]),
        )


@strawberry.type
class UnresolvedMention:
    """One entity mention without a canonical link."""

    mention_id: strawberry.ID
    surface: str
    entity_type: str
    char_start: int
    char_end: int
    cursor: str

    @classmethod
    def from_row(cls, row: Row) -> UnresolvedMention:
        """Build an ``UnresolvedMention`` from a read-adapter row."""
        return cls(
            mention_id=strawberry.ID(str(row["mention_id"])),
            surface=row.get("surface") or "",
            entity_type=row["entity_type"],
            char_start=row["char_start"],
            char_end=row["char_end"],
            cursor=row.get("cursor") or "",
        )


@strawberry.type
class ResolutionReview:
    """One pending or decided resolution-review row."""

    id: strawberry.ID
    mention_id: strawberry.ID
    candidates: JSON
    proposed_canonical_id: strawberry.ID | None
    proposed_canonical: CanonicalEntity | None
    confidence: float | None
    status: str
    reason: str | None
    created_at: datetime
    decided_at: datetime | None
    cursor: str

    @classmethod
    def from_row(cls, row: Row) -> ResolutionReview:
        """Build a ``ResolutionReview`` from a read-adapter row."""
        proposed_id = row.get("proposed_canonical_id")
        proposed = None
        if proposed_id is not None and row.get("proposed_preferred") is not None:
            proposed = CanonicalEntity(
                id=strawberry.ID(str(proposed_id)),
                preferred=row["proposed_preferred"],
                entity_type=row["proposed_entity_type"],
                wikidata_id=row.get("proposed_wikidata_id"),
                description=None,
                lang_primary=None,
                forms=[],
                cursor=str(proposed_id),
            )
        confidence = row.get("confidence")
        return cls(
            id=strawberry.ID(str(row["id"])),
            mention_id=strawberry.ID(str(row["mention_id"])),
            candidates=cast(JSON, row.get("candidates") or []),
            proposed_canonical_id=(
                strawberry.ID(str(proposed_id)) if proposed_id is not None else None
            ),
            proposed_canonical=proposed,
            confidence=float(confidence) if confidence is not None else None,
            status=row["status"],
            reason=row.get("reason"),
            created_at=row["created_at"],
            decided_at=row.get("decided_at"),
            cursor=row.get("cursor") or "",
        )
