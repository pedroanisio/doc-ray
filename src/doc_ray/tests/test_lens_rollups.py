"""Unit tests for the pure lens-rollup domain (issue #10, FR-3).

No database, no network. The correctness of hyper-context lives here: the
relationship-strength measure (NPMI, Bouma 2009), significance ranking, and the
**bounded, cycle-safe** N-hop traversal — pinned with hand-computed values before any
SQL exists (mandatory TDD).
"""

from __future__ import annotations

import math

import pytest

from doc_ray.domain import entity_profiles, lens_rollups
from doc_ray.domain.lens_rollups import (
    MAX_HOPS,
    npmi,
    rank_by_significance,
    related_within_hops,
)

# --- NPMI strength (Bouma 2009) --------------------------------------------


def test_npmi_of_a_hand_computed_case():
    # total=10 sentences; a in 5, b in 4, together in 3.
    # p_ab=0.3, p_a=0.5, p_b=0.4 ; pmi=log2(0.3/0.2)=log2(1.5) ; npmi=pmi/-log2(0.3)
    expected = math.log2(0.3 / (0.5 * 0.4)) / (-math.log2(0.3))
    assert npmi(joint=3, marginal_a=5, marginal_b=4, total=10) == pytest.approx(expected)
    assert 0 < expected < 1  # positively associated but not perfect


def test_npmi_is_one_when_always_co_occurring():
    # a and b each occur in all 6 sentences and always together → perfect association.
    assert npmi(joint=6, marginal_a=6, marginal_b=6, total=6) == pytest.approx(1.0)


def test_npmi_is_zero_for_independence():
    # p_ab == p_a * p_b  ⇒ pmi 0 ⇒ npmi 0. a in 5/10, b in 4/10, joint 2/10 = 0.5*0.4.
    assert npmi(joint=2, marginal_a=5, marginal_b=4, total=10) == pytest.approx(0.0, abs=1e-9)


def test_npmi_is_negative_when_dissociated():
    # joint far below chance → negative npmi (down to -1).
    assert npmi(joint=1, marginal_a=8, marginal_b=8, total=10) < 0


def test_npmi_is_symmetric():
    assert npmi(3, 5, 4, 10) == pytest.approx(npmi(3, 4, 5, 10))


def test_npmi_bounds_are_respected():
    for j, a, b, t in [(1, 9, 9, 10), (3, 5, 4, 10), (5, 5, 5, 5), (1, 1, 1, 100)]:
        v = npmi(j, a, b, t)
        assert -1.0 - 1e-9 <= v <= 1.0 + 1e-9, (j, a, b, t, v)


def test_npmi_degenerate_inputs_are_zero_not_errors():
    assert npmi(joint=0, marginal_a=3, marginal_b=3, total=10) == 0.0  # never together (not stored)
    assert npmi(joint=2, marginal_a=0, marginal_b=3, total=10) == 0.0  # impossible marginal
    assert npmi(joint=2, marginal_a=3, marginal_b=3, total=0) == 0.0  # empty universe


# --- type consistency reuse (entity_profiles) ------------------------------


def test_dominant_type_share_reuses_entity_profiles():
    assert entity_profiles.dominant_type_share({"PERSON": 9, "ORG": 1}) == pytest.approx(0.9)
    assert entity_profiles.dominant_type_share({"GPE": 4}) == 1.0
    assert entity_profiles.dominant_type_share({}) == 0.0


# --- significance ranking ---------------------------------------------------


def test_rank_by_significance_orders_and_filters():
    rows = [
        {"canonical_id": 1, "mention_count": 5, "document_count": 2},
        {"canonical_id": 2, "mention_count": 9, "document_count": 1},
        {"canonical_id": 3, "mention_count": 5, "document_count": 3},
        {"canonical_id": 4, "mention_count": 1, "document_count": 1},
    ]
    ranked = rank_by_significance(rows, min_mentions=2)
    ids = [r["canonical_id"] for r in ranked]
    # mention_count desc, then document_count desc, then canonical_id asc; #4 filtered.
    assert ids == [2, 3, 1]


def test_rank_by_significance_limit_and_empty():
    assert rank_by_significance([], min_mentions=0) == []
    rows = [{"canonical_id": i, "mention_count": i, "document_count": 1} for i in range(1, 6)]
    assert [r["canonical_id"] for r in rank_by_significance(rows, limit=2)] == [5, 4]


# --- bounded, cycle-safe N-hop traversal -----------------------------------


def _chain() -> dict[int, set[int]]:
    # A(1)-B(2)-C(3)-D(4)-E(5), undirected.
    return {1: {2}, 2: {1, 3}, 3: {2, 4}, 4: {3, 5}, 5: {4}}


def test_traversal_returns_hop_distances_bfs():
    out = related_within_hops(_chain(), start=1, max_hops=3)
    assert out == [(2, 1), (3, 2), (4, 3)]  # E(5) is 4 hops away → excluded


def test_traversal_hop_cap_is_enforced_not_clamped():
    with pytest.raises(ValueError, match="hops"):
        related_within_hops(_chain(), start=1, max_hops=MAX_HOPS + 1)
    with pytest.raises(ValueError, match="hops"):
        related_within_hops(_chain(), start=1, max_hops=0)


def test_traversal_terminates_on_a_cycle():
    triangle = {1: {2, 3}, 2: {1, 3}, 3: {1, 2}}  # A-B-C-A
    out = related_within_hops(triangle, start=1, max_hops=3)
    # B and C are both 1 hop from A; traversal must terminate (no infinite loop).
    assert out == [(2, 1), (3, 1)]


def test_traversal_is_deterministic_and_shortest_path():
    # A connects to C directly and via B; C's distance is the shortest (1), not 2.
    adj = {1: {2, 3}, 2: {1, 3}, 3: {1, 2}}
    assert related_within_hops(adj, start=1, max_hops=3) == [(2, 1), (3, 1)]


def test_traversal_limit_and_isolated_node():
    out = related_within_hops(_chain(), start=1, max_hops=3, limit=2)
    assert out == [(2, 1), (3, 2)]
    assert related_within_hops({1: set()}, start=1, max_hops=2) == []
    assert related_within_hops({}, start=99, max_hops=2) == []


def test_max_hops_constant_is_three():
    assert MAX_HOPS == 3


def test_no_unbounded_traversal_primitive_is_exposed():
    # The block forbids an unbounded graph walk (excluded scope); none is offered.
    for forbidden in ("walk", "traverse_all", "full_closure", "shortest_paths_all"):
        assert not hasattr(lens_rollups, forbidden), forbidden
