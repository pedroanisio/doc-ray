"""GraphQL types for persisted NLP annotation layers."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

import strawberry

Row = dict[str, Any]


@strawberry.type
class AnnotationRun:
    """One persisted annotation run for a document."""

    id: strawberry.ID
    layer: str
    tool: str
    tool_version: str | None
    created_at: datetime
    cursor: str

    @classmethod
    def from_row(cls, row: Row) -> AnnotationRun:
        """Build an ``AnnotationRun`` from a read-adapter row."""
        return cls(
            id=strawberry.ID(str(row["id"])),
            layer=row["layer"],
            tool=row["tool"],
            tool_version=row.get("tool_version"),
            created_at=row["created_at"],
            cursor=row.get("cursor") or "",
        )


@strawberry.type
class TokenFeature:
    """One UD morphology feature attached to a token."""

    feature: str
    value: str

    @classmethod
    def from_row(cls, row: Row) -> TokenFeature:
        """Build a ``TokenFeature`` from an aggregated feature row."""
        return cls(feature=row["feature"], value=row["value"])


@strawberry.type
class Token:
    """One token row with its span surface and morphology."""

    id: strawberry.ID
    annotation_id: strawberry.ID
    ordinal: int
    surface: str
    upos: str | None
    xpos: str | None
    lexeme_uid: strawberry.ID | None
    char_start: int
    char_end: int
    features: list[TokenFeature]
    cursor: str

    @classmethod
    def from_row(cls, row: Row) -> Token:
        """Build a ``Token`` from a read-adapter row."""
        features = cast(list[Row], row.get("features") or [])
        lexeme_uid = row.get("lexeme_uid")
        return cls(
            id=strawberry.ID(str(row["id"])),
            annotation_id=strawberry.ID(str(row["annotation_id"])),
            ordinal=row["ordinal"],
            surface=row.get("surface") or "",
            upos=row.get("upos"),
            xpos=row.get("xpos"),
            lexeme_uid=strawberry.ID(str(lexeme_uid)) if lexeme_uid is not None else None,
            char_start=row["char_start"],
            char_end=row["char_end"],
            features=[TokenFeature.from_row(feature) for feature in features],
            cursor=row.get("cursor") or "",
        )


@strawberry.type
class DependencyEdge:
    """One dependency edge between tokens."""

    annotation_id: strawberry.ID
    dependent_token_id: strawberry.ID
    head_token_id: strawberry.ID | None
    deprel: str
    cursor: str

    @classmethod
    def from_row(cls, row: Row) -> DependencyEdge:
        """Build a ``DependencyEdge`` from a read-adapter row."""
        head = row.get("head_token_id")
        return cls(
            annotation_id=strawberry.ID(str(row["annotation_id"])),
            dependent_token_id=strawberry.ID(str(row["dependent_token_id"])),
            head_token_id=strawberry.ID(str(head)) if head is not None else None,
            deprel=row["deprel"],
            cursor=row.get("cursor") or "",
        )


@strawberry.type
class Chunk:
    """One shallow syntax chunk."""

    id: strawberry.ID
    annotation_id: strawberry.ID
    chunk_type: str
    surface: str
    char_start: int
    char_end: int
    cursor: str

    @classmethod
    def from_row(cls, row: Row) -> Chunk:
        """Build a ``Chunk`` from a read-adapter row."""
        return cls(
            id=strawberry.ID(str(row["id"])),
            annotation_id=strawberry.ID(str(row["annotation_id"])),
            chunk_type=row["chunk_type"],
            surface=row.get("surface") or "",
            char_start=row["char_start"],
            char_end=row["char_end"],
            cursor=row.get("cursor") or "",
        )


@strawberry.type
class CorefMention:
    """One mention inside a coreference chain."""

    span_id: strawberry.ID
    surface: str

    @classmethod
    def from_row(cls, row: Row) -> CorefMention:
        """Build a ``CorefMention`` from an aggregated mention row."""
        return cls(span_id=strawberry.ID(str(row["span_id"])), surface=row.get("surface") or "")


@strawberry.type
class Constituent:
    """One Layer-9 phrase-structure node.

    ``parent_id`` is null for the sentence root (``S``). Nodes arrive outermost
    first, so a client can rebuild the tree by streaming rather than sorting.
    """

    id: strawberry.ID
    annotation_id: strawberry.ID
    parent_id: strawberry.ID | None
    label: str
    surface: str
    char_start: int
    char_end: int
    cursor: str

    @classmethod
    def from_row(cls, row: Row) -> Constituent:
        """Build a ``Constituent`` from a read-adapter row."""
        parent = row.get("parent_id")
        return cls(
            id=strawberry.ID(str(row["id"])),
            annotation_id=strawberry.ID(str(row["annotation_id"])),
            parent_id=strawberry.ID(str(parent)) if parent else None,
            label=row["label"],
            surface=row.get("surface") or "",
            char_start=row["char_start"],
            char_end=row["char_end"],
            cursor=row["cursor"],
        )


@strawberry.type
class SrlArgument:
    """One labelled argument of a Layer-14 frame (``ARG0``, ``ARGM-LOC``, ...)."""

    role: str
    surface: str
    char_start: int
    char_end: int


@strawberry.type
class SrlFrame:
    """One Layer-14 predicate with its arguments.

    ``frame`` is the predicate's LEMMA, not a PropBank roleset: the ``.01`` in
    ``found.01`` encodes a sense distinction this deterministic derivation does not
    make, so emitting it would assert a sense that was never determined.
    """

    id: strawberry.ID
    annotation_id: strawberry.ID
    frame: str
    surface: str
    char_start: int
    char_end: int
    arguments: list[SrlArgument]
    cursor: str

    @classmethod
    def from_row(cls, row: Row) -> SrlFrame:
        """Build an ``SrlFrame`` (with its arguments) from a read-adapter row."""
        return cls(
            id=strawberry.ID(str(row["id"])),
            annotation_id=strawberry.ID(str(row["annotation_id"])),
            frame=row["frame"],
            surface=row.get("surface") or "",
            char_start=row["char_start"],
            char_end=row["char_end"],
            arguments=[
                SrlArgument(
                    role=a["role"],
                    surface=a.get("surface") or "",
                    char_start=a["char_start"],
                    char_end=a["char_end"],
                )
                for a in row.get("arguments") or []
            ],
            cursor=row["cursor"],
        )


@strawberry.type
class CorefChain:
    """One coreference chain and its mentions."""

    id: strawberry.ID
    annotation_id: strawberry.ID
    mentions: list[CorefMention]
    cursor: str

    @classmethod
    def from_row(cls, row: Row) -> CorefChain:
        """Build a ``CorefChain`` from a read-adapter row."""
        mentions = cast(list[Row], row.get("mentions") or [])
        return cls(
            id=strawberry.ID(str(row["id"])),
            annotation_id=strawberry.ID(str(row["annotation_id"])),
            mentions=[CorefMention.from_row(mention) for mention in mentions],
            cursor=row.get("cursor") or "",
        )


@strawberry.type
class EntityLink:
    """One entity-link row with the mention surface."""

    mention_id: strawberry.ID
    scheme: str
    kb_id: str
    confidence: float | None
    entity_type: str
    surface: str
    char_start: int
    char_end: int
    cursor: str

    @classmethod
    def from_row(cls, row: Row) -> EntityLink:
        """Build an ``EntityLink`` from a read-adapter row."""
        confidence = row.get("confidence")
        return cls(
            mention_id=strawberry.ID(str(row["mention_id"])),
            scheme=row["scheme"],
            kb_id=row["kb_id"],
            confidence=float(confidence) if confidence is not None else None,
            entity_type=row["entity_type"],
            surface=row.get("surface") or "",
            char_start=row["char_start"],
            char_end=row["char_end"],
            cursor=row.get("cursor") or "",
        )
