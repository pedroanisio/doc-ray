"""Allusion edges — typed cross-lens links that never assert identity (issue #9).

An allusion is an **edge, not an equality**: the Paris in a novel *alludes to* the
real Paris; it is not the real Paris. The projection engine (issue #8) deliberately
keeps fictional and real identities separate — that is what prevents fabricated merges
(invariant 2) — but separation alone loses real knowledge. This module names the
honest relationship: a typed, directional link that preserves both entities and makes
the connection queryable, without ever collapsing them.

Pure (stdlib only, no I/O). The load-bearing invariant here is **negative**, and it
is enforced by absence as much as by code:

* An allusion is **never an identity claim.** :func:`is_identity_claim` returns
  ``False`` by construction, and nothing here reads or writes projection / resolution
  / canonical state. Recording an edge changes nothing about identity (issue §4).
* Allusions are **never transitive.** ``A alludes_to B`` and ``B alludes_to C`` does
  **not** yield ``A alludes_to C`` or ``A ≡ C``. This module deliberately exposes no
  edge-closure / composition / traversal primitive — the absence is the guarantee.

⚠ ARCHITECTURAL CONTRACT (PALS's LAW): a machine-proposed edge is a PROPOSAL. Every
edge a heuristic/model emits lands ``status='proposed'``; only a human/agent review
confirms it. An unverified allusion is untrusted metadata, never asserted knowledge.
"""

from __future__ import annotations

import enum


class EdgeType(str, enum.Enum):
    """The typed relations an allusion may carry (issue §4; DB CHECK mirrors this).

    All are *depiction/reference* relations — a fiction (or one work) pointing at a
    real-world referent or another work. None of them is (or may be read as) an
    identity equation; that is the invariant the whole block defends.
    """

    ALLUDES_TO = "alludes_to"  # a mention gestures at a referent without naming it as such
    DEPICTS = "depicts"  # a work portrays a (real or other-work) entity
    REFERENCES = "references"  # an explicit citation/mention of another entity or work
    ADAPTED_FROM = "adapted_from"  # this work is an adaptation of another
    INSPIRED_BY = "inspired_by"  # a looser creative lineage


#: The edge-type vocabulary as plain strings — single source for the migration's
#: ``CHECK (edge_type IN (...))`` and for API/MCP validation.
EDGE_TYPES: frozenset[str] = frozenset(t.value for t in EdgeType)

#: Review lifecycle (mirrors the DB CHECK on ``entity_allusion.status``).
STATUS_PROPOSED = "proposed"
STATUS_CONFIRMED = "confirmed"
STATUS_REJECTED = "rejected"


class Direction(str, enum.Enum):
    """Which edges of an anchor entity to return.

    ``outgoing`` — edges *from* the anchor (what it alludes to). ``incoming`` — edges
    *to* the anchor (what alludes to it). ``both`` — either endpoint matches the
    anchor. Direction is a *query* concern; it never implies the edge is symmetric.
    """

    OUTGOING = "outgoing"
    INCOMING = "incoming"
    BOTH = "both"


def is_valid_edge_type(edge_type: str) -> bool:
    """Return whether *edge_type* is one of the allowed relations."""
    return edge_type in EDGE_TYPES


def is_self_edge(
    from_canonical_id: int, from_lens_id: str, to_canonical_id: int, to_lens_id: str
) -> bool:
    """Return whether the endpoints are the same entity in the same lens.

    Exactly the DB CHECK ``(from_canonical_id <> to_canonical_id OR from_lens_id <>
    to_lens_id)`` negated: a self-edge is meaningless (an entity alluding to itself in
    its own lens). The *same* canonical across *different* lenses is a legitimate
    cross-lens edge and is **not** a self-edge.
    """
    return from_canonical_id == to_canonical_id and from_lens_id == to_lens_id


def validate_edge(
    *,
    from_canonical_id: int,
    from_lens_id: str,
    to_canonical_id: int,
    to_lens_id: str,
    edge_type: str,
) -> None:
    """Raise :class:`ValueError` if the edge is invalid; return ``None`` if it is sound.

    Two rules, both re-checked by the database (the app pre-check is a clean error,
    never the only guard): the ``edge_type`` must be in :data:`EDGE_TYPES`, and the
    edge must not be a self-edge. No rule here asserts or permits identity — that is
    structurally impossible for an edge.
    """
    if not is_valid_edge_type(edge_type):
        raise ValueError(f"unknown edge_type {edge_type!r}; expected one of {sorted(EDGE_TYPES)}")
    if is_self_edge(from_canonical_id, from_lens_id, to_canonical_id, to_lens_id):
        raise ValueError(
            "an allusion cannot point an entity at itself within one lens "
            "(same canonical_id and lens_id)"
        )


def is_identity_claim(edge_type: str | None = None) -> bool:
    """Return ``False`` — an allusion is never an identity claim (the block's invariant).

    Named and total so the guarantee is *testable*: whatever the edge type, an
    allusion records a relationship, never an equality. Consumers (projection,
    resolution, canonical) must treat every edge as additive metadata only.
    """
    return False


def normalize_direction(value: str | None) -> Direction:
    """Parse a direction argument case-/whitespace-insensitively; default ``both``.

    Raises :class:`ValueError` on anything outside the vocabulary so a bad API/MCP
    argument fails loudly instead of silently returning one side.
    """
    if value is None:
        return Direction.BOTH
    clean = value.strip().lower()
    try:
        return Direction(clean)
    except ValueError:
        raise ValueError(
            f"unknown direction {value!r}; expected one of {[d.value for d in Direction]}"
        ) from None
