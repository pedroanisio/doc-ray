"""GraphQL types for sentence substring/phrase search (#45)."""

from __future__ import annotations

import strawberry

from doc_ray.domain.phrase_profile import PROFILE_SURFACE_WORD_V1
from doc_ray.entrypoints.api.document_types import Row


@strawberry.type
class SentenceOccurrence:
    """One exact half-open phrase occurrence in absolute document offsets."""

    char_start: int
    char_end: int

    @classmethod
    def from_row(cls, row: Row) -> SentenceOccurrence:
        """Build an occurrence from adapter snake-case offsets."""
        return cls(char_start=int(row["char_start"]), char_end=int(row["char_end"]))


@strawberry.type
class SentenceHit:
    """A sentence search hit with KWIC and optional verified occurrences."""

    id: strawberry.ID
    document_id: strawberry.ID
    title: str
    ordinal: int
    text: str
    pre: str
    hit: str
    post: str
    cursor: str
    occurrences: list[SentenceOccurrence]
    occurrence_count: int

    @classmethod
    def from_row(cls, row: Row) -> SentenceHit:
        """Build a hit; substring rows intentionally carry no occurrences."""
        occurrences = [SentenceOccurrence.from_row(item) for item in row.get("occurrences", [])]
        return cls(
            id=strawberry.ID(str(row["id"])),
            document_id=strawberry.ID(str(row["doc_id"])),
            title=row["title"],
            ordinal=int(row["ordinal"]),
            text=row["text"],
            pre=row.get("pre") or "",
            hit=row.get("hit") or "",
            post=row.get("post") or "",
            cursor=row.get("cursor") or "",
            occurrences=occurrences,
            occurrence_count=int(row.get("occurrence_count") or 0),
        )


@strawberry.type
class SentenceSearchPage:
    """Sentence hits plus evidence about bounded phrase candidate verification."""

    hits: list[SentenceHit]
    match_profile: str | None
    candidate_scanned: int
    candidate_limit: int | None
    candidate_limit_reached: bool

    @classmethod
    def from_rows(cls, rows: list[Row], *, phrase: bool) -> SentenceSearchPage:
        """Build a non-breaking envelope around adapter rows."""
        return cls(
            hits=[SentenceHit.from_row(row) for row in rows],
            match_profile=PROFILE_SURFACE_WORD_V1 if phrase else None,
            candidate_scanned=int(getattr(rows, "candidate_scanned", len(rows))),
            candidate_limit=(int(getattr(rows, "candidate_limit", 0)) if phrase else None),
            candidate_limit_reached=bool(getattr(rows, "candidate_limit_reached", False)),
        )
