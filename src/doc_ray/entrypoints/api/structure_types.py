"""GraphQL types for the document outline and reconciled entity profiles (BL-146)."""

from __future__ import annotations

from typing import Any

import strawberry

Row = dict[str, Any]


@strawberry.type
class OutlineEntry:
    """One addressable region of a document.

    ``ordinalStart``/``ordinalEnd`` are inclusive and feed straight into
    ``Document.sentences(fromOrdinal:, toOrdinal:)``; ``pageStart`` feeds the
    ``page`` argument instead where the document has layout.
    """

    level: int
    title: str
    ordinal_start: int
    ordinal_end: int
    char_start: int
    char_end: int
    page_start: int | None
    page_end: int | None

    @classmethod
    def from_row(cls, row: Row) -> OutlineEntry:
        """Build an ``OutlineEntry`` from a read-adapter row."""
        return cls(
            level=int(row["level"]),
            title=row["title"],
            ordinal_start=int(row["ordinal_start"]),
            ordinal_end=int(row["ordinal_end"]),
            char_start=int(row["char_start"]),
            char_end=int(row["char_end"]),
            page_start=row.get("page_start"),
            page_end=row.get("page_end"),
        )


@strawberry.type
class DocumentOutline:
    """A document's navigable map, with the provenance of its structure attached.

    ``source`` is the honest part: ``headings`` means detected document
    structure, ``uniform`` means an even split of the sentences (a navigation
    grid, not a table of contents), and ``empty`` means there was nothing to map.
    A caller must be able to tell those apart without parsing ``note``.
    """

    source: str
    total_sentences: int
    entry_count: int
    note: str | None
    entries: list[OutlineEntry]

    @classmethod
    def from_row(cls, row: Row) -> DocumentOutline:
        """Build a ``DocumentOutline`` from a read-adapter row."""
        return cls(
            source=row["source"],
            total_sentences=int(row["total_sentences"]),
            entry_count=int(row["entry_count"]),
            note=row.get("note"),
            entries=[OutlineEntry.from_row(entry) for entry in row["entries"]],
        )


@strawberry.type
class EntityTypeCount:
    """One entity type the annotator assigned, and how often."""

    entity_type: str
    count: int


@strawberry.type
class EntityProfile:
    """One reconciled entity: its mentions, spellings, and type disagreement.

    ``typeConsistency`` is the dominant type's measured share of the entity's
    mentions — arithmetic over the annotator's output, never a model confidence.
    A low value is the signal that the type is contested (``Arrakis`` scores 0.41
    across LOC/GPE/PERSON/NORP). ``resolved`` says whether the entity is backed by
    the canonical registry; ``false`` means this is reconciled NER and nothing
    more.
    """

    surface: str
    normalized_form: str
    mentions: int
    dominant_type: str
    type_spread: list[EntityTypeCount]
    type_consistency: float
    surface_forms: list[str]
    canonical_id: strawberry.ID | None
    canonical_name: str | None
    wikidata_id: str | None
    resolved: bool

    @classmethod
    def from_row(cls, row: Row) -> EntityProfile:
        """Build an ``EntityProfile`` from a read-adapter row."""
        return cls(
            surface=row["surface"],
            normalized_form=row["normalized_form"],
            mentions=int(row["mentions"]),
            dominant_type=row["dominant_type"],
            type_spread=[
                EntityTypeCount(entity_type=t["entity_type"], count=int(t["count"]))
                for t in row["type_spread"]
            ],
            type_consistency=float(row["type_consistency"]),
            surface_forms=list(row["surface_forms"]),
            canonical_id=(
                strawberry.ID(str(row["canonical_id"])) if row.get("canonical_id") else None
            ),
            canonical_name=row.get("canonical_name"),
            wikidata_id=row.get("wikidata_id"),
            resolved=bool(row["resolved"]),
        )
