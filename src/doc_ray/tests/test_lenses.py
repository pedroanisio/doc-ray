"""Unit tests for the pure lens domain (Block B, issue #5).

No database, no network — the reserved-name rules, name normalization, and the
cycle-detection algorithm over ``(id, parent_id)`` pairs are all deterministic
and testable in isolation. The DB trigger in migration 118 mirrors
:func:`would_create_cycle`; this module is that algorithm's tested source of
truth (§8 of the issue: self-reference and a 3-node cycle are required cases).
"""

from __future__ import annotations

import pytest

from doc_ray.domain import lenses


def test_global_is_the_reserved_lens_name() -> None:
    assert lenses.GLOBAL_LENS_NAME == "global"


@pytest.mark.parametrize("name", ["global", "GLOBAL", " Global ", "\tglobal\n"])
def test_reserved_name_is_recognized_case_and_space_insensitively(name: str) -> None:
    assert lenses.is_reserved_name(name) is True


@pytest.mark.parametrize("name", ["tolkien", "global lens", "the global", "globals"])
def test_ordinary_names_are_not_reserved(name: str) -> None:
    assert lenses.is_reserved_name(name) is False


def test_normalize_name_trims_surrounding_whitespace() -> None:
    assert lenses.normalize_name("  Tolkien Legendarium  ") == "Tolkien Legendarium"


@pytest.mark.parametrize("name", ["", "   ", "\t\n"])
def test_normalize_name_rejects_empty(name: str) -> None:
    with pytest.raises(ValueError):
        lenses.normalize_name(name)


# --- cycle detection over (id, parent_id) pairs ---------------------------


def test_find_cycle_returns_none_for_empty_graph() -> None:
    assert lenses.find_cycle({}) is None


def test_find_cycle_returns_none_for_a_root_only_graph() -> None:
    assert lenses.find_cycle({"a": None, "b": None}) is None


def test_find_cycle_returns_none_for_an_acyclic_chain() -> None:
    # a -> b -> c -> (root)
    assert lenses.find_cycle({"a": "b", "b": "c", "c": None}) is None


def test_find_cycle_detects_a_self_reference() -> None:
    cycle = lenses.find_cycle({"a": "a"})
    assert cycle is not None
    assert "a" in cycle


def test_find_cycle_detects_a_three_node_cycle() -> None:
    cycle = lenses.find_cycle({"a": "b", "b": "c", "c": "a"})
    assert cycle is not None
    assert set(cycle) >= {"a", "b", "c"}


def test_find_cycle_is_order_independent() -> None:
    forward = lenses.find_cycle({"a": "b", "b": "c", "c": "a"})
    shuffled = lenses.find_cycle({"c": "a", "a": "b", "b": "c"})
    assert (forward is None) == (shuffled is None)
    assert forward is not None and shuffled is not None


def test_would_create_cycle_allows_a_fresh_parent() -> None:
    # a and b are both roots; making b the parent of a is fine.
    assert lenses.would_create_cycle({"a": None, "b": None}, "a", "b") is False


def test_would_create_cycle_rejects_a_direct_back_edge() -> None:
    # b's parent is already a; making a's parent b closes a <-> b.
    assert lenses.would_create_cycle({"a": None, "b": "a"}, "a", "b") is True


def test_would_create_cycle_rejects_self_parenting() -> None:
    assert lenses.would_create_cycle({"a": None}, "a", "a") is True


def test_would_create_cycle_rejects_a_transitive_back_edge() -> None:
    # c -> b -> a ; making a's parent c closes a -> c -> b -> a.
    assert lenses.would_create_cycle({"a": None, "b": "a", "c": "b"}, "a", "c") is True


def test_would_create_cycle_ignores_a_null_parent() -> None:
    assert lenses.would_create_cycle({"a": "b", "b": None}, "a", None) is False
