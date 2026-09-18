"""Unit tests for the pure genre → resolution-scope policy (issue #6).

No database, no network. The policy is a total, deterministic pure function; these
tests pin the full genre × entity-type matrix, totality on unknown inputs, and the
conservative default that a wrong genre must degrade to a visible/repairable GLOBAL
link rather than a silent LOCAL fragmentation.
"""

from __future__ import annotations

import pytest

from doc_ray.domain.genre_policy import (
    ALWAYS_GLOBAL_TYPES,
    FICTION_LOCAL_TYPES,
    Genre,
    ResolutionScope,
    resolution_scope,
)


def test_fiction_local_types_resolve_local_only_inside_fiction():
    for etype in FICTION_LOCAL_TYPES:
        assert resolution_scope(Genre.FICTION, etype) is ResolutionScope.LOCAL, etype
        # The very same type is GLOBAL in a non-fiction work.
        assert resolution_scope(Genre.NON_FICTION, etype) is ResolutionScope.GLOBAL, etype


def test_always_global_types_are_global_even_inside_fiction():
    # Measurements and calendar facts are not fictional even in a novel.
    for etype in ALWAYS_GLOBAL_TYPES:
        assert resolution_scope(Genre.FICTION, etype) is ResolutionScope.GLOBAL, etype


def test_the_two_partitions_are_disjoint():
    assert FICTION_LOCAL_TYPES.isdisjoint(ALWAYS_GLOBAL_TYPES)


def test_headline_cases_from_the_spec():
    assert resolution_scope(Genre.FICTION, "PERSON") is ResolutionScope.LOCAL
    assert resolution_scope(Genre.FICTION, "DATE") is ResolutionScope.GLOBAL
    assert resolution_scope(Genre.NON_FICTION, "PERSON") is ResolutionScope.GLOBAL


@pytest.mark.parametrize("genre", [Genre.MIXED, Genre.UNKNOWN])
def test_mixed_and_unknown_genres_are_conservatively_global(genre):
    # A wrong global link is visible and repairable; a wrong local fragmentation
    # is silent — so anything but confirmed fiction resolves GLOBAL.
    for etype in list(FICTION_LOCAL_TYPES) + list(ALWAYS_GLOBAL_TYPES) + ["MADE_UP"]:
        assert resolution_scope(genre, etype) is ResolutionScope.GLOBAL, (genre, etype)


def test_unknown_entity_type_under_fiction_defaults_global():
    # Totality: an unrecognized label must still return a scope, and the safe one.
    assert resolution_scope(Genre.FICTION, "TOTALLY_UNKNOWN_LABEL") is ResolutionScope.GLOBAL
    assert resolution_scope(Genre.FICTION, "") is ResolutionScope.GLOBAL


def test_entity_type_matching_is_case_insensitive():
    assert resolution_scope(Genre.FICTION, "person") is ResolutionScope.LOCAL
    assert resolution_scope(Genre.FICTION, "  Person  ") is ResolutionScope.LOCAL


def test_function_is_total_over_the_full_matrix_and_deterministic():
    observed_types = sorted(FICTION_LOCAL_TYPES | ALWAYS_GLOBAL_TYPES | {"MISC", "NORP", "x"})
    for genre in Genre:
        for etype in observed_types:
            first = resolution_scope(genre, etype)
            assert isinstance(first, ResolutionScope)
            # Deterministic: identical inputs yield identical outputs.
            assert resolution_scope(genre, etype) is first
