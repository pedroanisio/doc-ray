"""Lens domain — the naming and integrity rules for entity-resolution scopes.

A *lens* (issue #5, the Substrate·Lens·Projection architecture in
``docs/proposals/entity-fabric-substrate-lens-projection.md``) is a named scope
under which a later projection layer will interpret entity identity. Block B
ships the vocabulary only: a lens is a ``document_collections`` row flagged
``is_scope`` — *promoted*, not a parallel structure — and nothing here changes
how any entity resolves today.

This module is pure (stdlib only, no I/O): the reserved-name rule and the
cycle-detection algorithm over ``(id, parent_id)`` pairs are deterministic and
unit-tested without a database. The DB trigger in migration 121 enforces the
same cycle rule in PL/pgSQL; :func:`would_create_cycle` is its tested
specification, so the guarantee — *a parent lens chain never loops* — is
provable in isolation and re-checked by the API before it reaches the trigger.
"""

from __future__ import annotations

from collections.abc import Mapping

#: The reserved lens that always exists and cannot be deleted or renamed. It is
#: the default scope for real-world (Wikidata-linkable) identities; scope lenses
#: are the fictional/work-local scopes layered beside it. Seeded idempotently by
#: migration 121 and protected there by a trigger — the string is single-sourced
#: here so the API can reject a rename/demote with a clean error first.
GLOBAL_LENS_NAME = "global"


def is_reserved_name(name: str) -> bool:
    """Return whether *name* is the reserved ``global`` lens name.

    Case- and surrounding-whitespace-insensitive: ``"Global"`` and ``" global "``
    are the reserved name, because the ``document_collections.name`` uniqueness
    the seed relies on is exact and a near-miss should still be refused loudly.
    """
    return name.strip().casefold() == GLOBAL_LENS_NAME


def normalize_name(name: str) -> str:
    """Return *name* trimmed of surrounding whitespace, or raise if it is empty.

    A lens name is the collection name; it must be a non-empty label so the
    ``UNIQUE`` name column and the reserved-name check both have something real
    to compare.
    """
    clean = name.strip()
    if not clean:
        raise ValueError("lens name cannot be empty")
    return clean


def _parent_chain_reaches(edges: Mapping[str, str | None], start: str | None, target: str) -> bool:
    """Return whether walking parents from *start* reaches *target*.

    Bounded by the number of edges — a malformed graph that already contains a
    cycle not involving *target* terminates via the ``visited`` guard rather
    than looping forever. Complexity: O(depth), at most O(nodes).
    """
    visited: set[str] = set()
    current = start
    while current is not None:
        if current == target:
            return True
        if current in visited:  # a pre-existing cycle elsewhere — stop, don't hang
            return False
        visited.add(current)
        current = edges.get(current)
    return False


def would_create_cycle(edges: Mapping[str, str | None], child: str, new_parent: str | None) -> bool:
    """Return whether pointing *child*'s parent at *new_parent* forms a cycle.

    *edges* maps every known lens id to its current ``parent_lens_id`` (``None``
    for a root). A ``None`` *new_parent* detaches the child and can never cycle.
    Self-parenting is the degenerate one-node cycle. Otherwise the edge closes a
    loop exactly when *child* is already an ancestor of *new_parent*.
    """
    if new_parent is None:
        return False
    if new_parent == child:
        return True
    return _parent_chain_reaches(edges, new_parent, child)


def find_cycle(edges: Mapping[str, str | None]) -> tuple[str, ...] | None:
    """Return one cycle in the parent graph as an id tuple, or ``None`` if acyclic.

    *edges* maps each lens id to its ``parent_lens_id``. Used to validate a whole
    graph (e.g. a test fixture or an imported set) rather than a single proposed
    edge; :func:`would_create_cycle` is the incremental check the API uses.
    Deterministic and order-independent — a graph is cyclic regardless of dict
    insertion order. Complexity: O(nodes) with memoized colours.
    """
    WHITE, GREY, BLACK = 0, 1, 2
    colour: dict[str, int] = {node: WHITE for node in edges}

    def visit(node: str, path: list[str]) -> tuple[str, ...] | None:
        colour[node] = GREY
        path.append(node)
        parent = edges.get(node)
        if parent is not None and parent in colour:
            if colour[parent] == GREY:  # back edge into the current path -> cycle
                return tuple(path[path.index(parent) :])
            if colour[parent] == WHITE:
                found = visit(parent, path)
                if found is not None:
                    return found
        colour[node] = BLACK
        path.pop()
        return None

    for start in edges:
        if colour[start] == WHITE:
            cycle = visit(start, [])
            if cycle is not None:
                return cycle
    return None
