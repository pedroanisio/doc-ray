"""Unit tests for the pure allusion-edge domain (issue #9, FR-2).

No database, no network. An allusion is an EDGE, never an identity equation — that
negative invariant is the whole point of the block, so it is pinned here before any
schema exists (mandatory TDD). The tests fix the edge-type vocabulary (which the DB
CHECK mirrors), self-edge rejection, direction semantics, and — load-bearing — that
the domain makes no identity claim and offers no transitive-closure primitive.
"""

from __future__ import annotations

import pytest

from doc_ray.domain import allusions
from doc_ray.domain.allusions import (
    EDGE_TYPES,
    STATUS_CONFIRMED,
    STATUS_PROPOSED,
    STATUS_REJECTED,
    Direction,
    EdgeType,
    is_identity_claim,
    is_self_edge,
    normalize_direction,
    validate_edge,
)


def test_edge_type_vocabulary_is_the_five_specified():
    assert {"alludes_to", "depicts", "references", "adapted_from", "inspired_by"} == EDGE_TYPES
    # The enum and the frozenset agree (single source for the DB CHECK).
    assert {t.value for t in EdgeType} == EDGE_TYPES


def test_status_constants():
    assert (STATUS_PROPOSED, STATUS_CONFIRMED, STATUS_REJECTED) == (
        "proposed",
        "confirmed",
        "rejected",
    )


# --- the negative invariant: an allusion is NEVER an identity claim ---------


def test_is_identity_claim_is_always_false():
    # By construction and by contract (invariant 2): recording an allusion changes
    # nothing about projection, resolution, or canonical identity.
    assert is_identity_claim() is False
    for etype in EDGE_TYPES:
        assert is_identity_claim(etype) is False


def test_no_transitive_closure_primitive_is_exposed():
    # Transitivity is deliberately absent (acceptance criterion): A->B and B->C must
    # never yield A==C or A->C. The domain offers no such composition to misuse.
    for forbidden in ("transitive_closure", "close", "compose_edges", "infer_edges", "traverse"):
        assert not hasattr(allusions, forbidden), forbidden


# --- self-edge rejection (mirrors the DB CHECK) ----------------------------


def test_self_edge_is_same_entity_in_same_lens_only():
    assert is_self_edge(5, "lensA", 5, "lensA") is True
    # Same canonical across DIFFERENT lenses is a legitimate cross-lens edge.
    assert is_self_edge(5, "lensA", 5, "lensB") is False
    # Different canonicals in the same lens is legitimate too.
    assert is_self_edge(5, "lensA", 6, "lensA") is False


def test_validate_edge_accepts_a_cross_lens_edge():
    validate_edge(
        from_canonical_id=5001,
        from_lens_id="fiction",
        to_canonical_id=84,
        to_lens_id="global",
        edge_type="alludes_to",
    )  # no raise


def test_validate_edge_rejects_a_self_edge():
    with pytest.raises(ValueError, match="itself"):
        validate_edge(
            from_canonical_id=5,
            from_lens_id="L",
            to_canonical_id=5,
            to_lens_id="L",
            edge_type="depicts",
        )


def test_validate_edge_rejects_an_unknown_type():
    with pytest.raises(ValueError, match="edge_type"):
        validate_edge(
            from_canonical_id=1,
            from_lens_id="A",
            to_canonical_id=2,
            to_lens_id="B",
            edge_type="is_the_same_as",  # smells like identity — forbidden
        )


# --- direction semantics ----------------------------------------------------


def test_direction_values():
    assert {d.value for d in Direction} == {"outgoing", "incoming", "both"}


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("outgoing", Direction.OUTGOING),
        ("INCOMING", Direction.INCOMING),
        ("  both ", Direction.BOTH),
        (None, Direction.BOTH),  # default
    ],
)
def test_normalize_direction(raw, expected):
    assert normalize_direction(raw) is expected


def test_normalize_direction_rejects_garbage():
    with pytest.raises(ValueError, match="direction"):
        normalize_direction("sideways")
