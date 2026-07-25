"""Strawberry result types for the shared briefing evidence contract."""

from __future__ import annotations

from typing import Any

import strawberry


@strawberry.type
class Cite:
    """A resolvable source handle, optionally targeting an exact sentence."""

    document_id: strawberry.ID
    sentence_ordinal: int | None
    ref: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Cite:
        """Map the shared service's snake-case citation shape."""
        return cls(
            document_id=strawberry.ID(str(value["document_id"])),
            sentence_ordinal=value.get("sentence_ordinal"),
            ref=str(value["ref"]),
        )


@strawberry.type
class BriefingSource:
    """One ranked document that contributed to the pack."""

    document_id: strawberry.ID
    title: str | None
    rank: float | None
    cite: Cite

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> BriefingSource:
        """Map a shared-service source."""
        return cls(
            document_id=strawberry.ID(str(value["document_id"])),
            title=value.get("title"),
            rank=value.get("rank"),
            cite=Cite.from_dict(value["cite"]),
        )


@strawberry.type
class BriefingPassage:
    """One ranked, exact-sentence-citable evidence passage."""

    document_id: strawberry.ID
    title: str | None
    sentence_ordinal: int | None
    text: str
    matched_terms: list[str]
    cite: Cite

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> BriefingPassage:
        """Map a shared-service passage."""
        return cls(
            document_id=strawberry.ID(str(value["document_id"])),
            title=value.get("title"),
            sentence_ordinal=value.get("sentence_ordinal"),
            text=str(value["text"]),
            matched_terms=list(value.get("matched_terms") or []),
            cite=Cite.from_dict(value["cite"]),
        )


@strawberry.type
class BriefingEntity:
    """A merged named-entity count across contributing documents."""

    text: str
    entity_type: str | None
    count: int

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> BriefingEntity:
        """Map a shared-service entity."""
        return cls(
            text=str(value["text"]),
            entity_type=value.get("entity_type"),
            count=int(value["count"]),
        )


@strawberry.type
class BriefingCoverage:
    """Counts for visible, returned sources and passages."""

    documents: int
    passages: int


@strawberry.type
class BriefingPack:
    """The complete gap-honest evidence contract exposed by GraphQL."""

    topic: str
    retrieval: str
    sources: list[BriefingSource]
    key_passages: list[BriefingPassage]
    entities: list[BriefingEntity]
    coverage: BriefingCoverage
    gaps: list[str]
    guidance: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> BriefingPack:
        """Map the shared service's nested contract without dropping fields."""
        coverage = value["coverage"]
        return cls(
            topic=str(value["topic"]),
            retrieval=str(value["retrieval"]),
            sources=[BriefingSource.from_dict(item) for item in value["sources"]],
            key_passages=[BriefingPassage.from_dict(item) for item in value["key_passages"]],
            entities=[BriefingEntity.from_dict(item) for item in value["entities"]],
            coverage=BriefingCoverage(
                documents=int(coverage["documents"]), passages=int(coverage["passages"])
            ),
            gaps=list(value["gaps"]),
            guidance=str(value["guidance"]),
        )
