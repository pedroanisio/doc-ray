"""Hyper-context domain — relationship strength, ranking, bounded traversal (issue #10).

Turns the projected entity graph into corpus-wide sensemaking, but keeps the *decisions*
here, pure and deterministic (stdlib only, no I/O). Three primitives:

1. :func:`npmi` — the relationship-strength measure. It is **Normalized Pointwise Mutual
   Information** (Bouma, 2009, "Normalized (Pointwise) Mutual Information in Collocation
   Extraction"), computed over sentence-level co-occurrence:

       pmi(a, b)  = log2( p(a,b) / (p(a) · p(b)) )
       npmi(a, b) = pmi(a, b) / ( -log2 p(a,b) )

   where p(x) is the fraction of the lens's co-occurrence-bearing sentences containing
   x, and p(a,b) the fraction containing both. NPMI ∈ [-1, 1]: **+1** perfect
   association (a and b always co-occur), **0** statistical independence, **−1** never
   together. Crucially it is normalized by the marginals, so a ubiquitous entity does
   **not** appear strongly related to everything — the property FR/D-2 requires and raw
   co-occurrence counts lack. Degenerate inputs (a pair that never co-occurs, a zero
   marginal, an empty universe) return ``0.0`` rather than raising.

2. :func:`rank_by_significance` — deterministic significance ordering of a lens's entities.

3. :func:`related_within_hops` — **bounded, cycle-safe** BFS over an in-memory adjacency
   map, with a hard hop cap (:data:`MAX_HOPS`). There is deliberately **no** unbounded
   walk / full-closure primitive (excluded scope): the absence is the guarantee.

⚠ PALS's LAW: a co-occurrence strength is a *structural measure*, never a claim of a
real-world relationship, and these rollups are precomputed — a consumer reads them with
their ``refreshed_at`` staleness stamp, not as live truth.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

#: Hard cap on relationship-traversal depth (FR-5). A request beyond this is rejected,
#: never silently clamped — an unbounded graph walk is out of scope by design.
MAX_HOPS = 3


def npmi(joint: int, marginal_a: int, marginal_b: int, total: int) -> float:
    """Return the NPMI of two entities' sentence co-occurrence (Bouma 2009); see module doc.

    Args:
        joint: sentences containing both a and b.
        marginal_a: sentences containing a.
        marginal_b: sentences containing b.
        total: sentences in the lens's co-occurrence universe.

    Returns:
        NPMI in ``[-1, 1]``. ``0.0`` for any degenerate input (``joint``/marginal/``total``
        non-positive), and exactly ``1.0`` when the pair always co-occurs (``p(a,b)=1``,
        where ``-log2 p(a,b)`` would be 0).
    """
    if joint <= 0 or marginal_a <= 0 or marginal_b <= 0 or total <= 0:
        return 0.0
    p_ab = joint / total
    p_a = marginal_a / total
    p_b = marginal_b / total
    if p_ab >= 1.0:  # co-occur in every window → perfect association (avoid /0)
        return 1.0
    pmi = math.log2(p_ab / (p_a * p_b))
    return pmi / (-math.log2(p_ab))


def rank_by_significance(
    entities: Iterable[Mapping[str, Any]], *, min_mentions: int = 0, limit: int | None = None
) -> list[Mapping[str, Any]]:
    """Rank a lens's entities by significance, deterministically.

    Order: ``mention_count`` desc, then ``document_count`` desc, then ``canonical_id``
    asc (a stable tie-break). Drops entities below *min_mentions*; applies *limit* after
    ranking. The database serves the same order via an index; this is its pure spec and
    the reranker for in-memory sets.
    """
    kept = [e for e in entities if int(e.get("mention_count", 0)) >= min_mentions]
    kept.sort(
        key=lambda e: (
            -int(e.get("mention_count", 0)),
            -int(e.get("document_count", 0)),
            int(e["canonical_id"]),
        )
    )
    return kept[:limit] if limit is not None else kept


def related_within_hops(
    adjacency: Mapping[int, Iterable[int]],
    start: int,
    *,
    max_hops: int,
    limit: int | None = None,
) -> list[tuple[int, int]]:
    """Return ``(canonical_id, hop_distance)`` reachable from *start* within *max_hops*.

    Breadth-first over the undirected *adjacency* map, so each returned node carries its
    **shortest** hop distance. Cycle-safe (a ``visited`` set — a cyclic graph terminates)
    and deterministic (neighbours visited in sorted order). *start* itself is excluded.

    Raises:
        ValueError: if ``max_hops`` is not in ``[1, MAX_HOPS]`` — the cap is enforced by
            rejection, never by silent clamping (FR-5 / acceptance criteria).
    """
    if not 1 <= max_hops <= MAX_HOPS:
        raise ValueError(f"hops must be between 1 and {MAX_HOPS}; got {max_hops}")
    visited: set[int] = {start}
    frontier: list[int] = [start]
    out: list[tuple[int, int]] = []
    for hop in range(1, max_hops + 1):
        next_frontier: list[int] = []
        for node in frontier:
            for neighbour in sorted(adjacency.get(node, ())):
                if neighbour in visited:
                    continue
                visited.add(neighbour)
                out.append((neighbour, hop))
                next_frontier.append(neighbour)
                if limit is not None and len(out) >= limit:
                    return out
        frontier = next_frontier
    return out


def build_adjacency(edges: Sequence[tuple[int, int]]) -> dict[int, set[int]]:
    """Build an undirected adjacency map from ``(from, to)`` relation pairs.

    Each pair (stored once as ``from < to``, migration 133) becomes edges in *both*
    directions, so traversal is symmetric regardless of which endpoint is the anchor.
    """
    adj: dict[int, set[int]] = {}
    for a, b in edges:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    return adj
