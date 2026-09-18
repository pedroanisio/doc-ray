"""Unit tests for the pure projection resolver (issue #8, FR-3).

No database, no network. ``project()`` is the correctness core of the whole block:
it decides *which entity a mention is, within a lens*, and every schema/adapter/API
layer merely persists and exposes what it returns. So the resolution law is pinned
here — exhaustively — before any SQL exists (mandatory TDD, issue §"Mandatory TDD").

The law (issue §5, FR-3):

    policy decides local vs global → local-first lookup → global fallback →
    explicit unresolved.

with two lens rules: the reserved ``global`` lens forces GLOBAL for every mention;
a scope lens applies the genre policy per entity type.
"""

from __future__ import annotations

from doc_ray.domain.genre_policy import Genre, ResolutionScope
from doc_ray.domain.projection import (
    METHOD_EXACT_FORM,
    METHOD_UNRESOLVED,
    SCOPE_GLOBAL,
    SCOPE_LOCAL,
    STATUS_PROPOSED,
    GlobalLink,
    Lens,
    Mention,
    Projection,
    local_key,
    project,
)

# --- fixtures -------------------------------------------------------------

_SCOPE_LENS = Lens(lens_id="lens-work", is_global=False)
_GLOBAL_LENS = Lens(lens_id="lens-global", is_global=True)


def _mention(mid: str = "m1", etype: str = "PERSON", surface: str = "Duncan") -> Mention:
    return Mention(mention_id=mid, entity_type=etype, surface=surface)


# --- scope constants pin the DB CHECK -------------------------------------


def test_scope_constants_match_resolution_scope_values():
    # entity_projection.scope CHECK (scope IN ('local','global')) is single-sourced.
    assert SCOPE_LOCAL == ResolutionScope.LOCAL.value == "local"
    assert SCOPE_GLOBAL == ResolutionScope.GLOBAL.value == "global"


# --- LOCAL resolution (fiction × fiction-local type) ----------------------


def test_fiction_local_type_resolves_to_the_lens_local_identity():
    m = _mention(surface="Duncan", etype="PERSON")
    p = project(
        m,
        _SCOPE_LENS,
        Genre.FICTION,
        local_candidates={local_key("Duncan", "PERSON"): 501},
        global_index={},
    )
    assert p == Projection(
        mention_id="m1",
        lens_id="lens-work",
        canonical_id=501,
        scope=SCOPE_LOCAL,
        method=METHOD_EXACT_FORM,
        confidence=1.0,
        status=STATUS_PROPOSED,
    )


def test_local_lookup_is_case_and_whitespace_insensitive():
    m = _mention(surface="  duncan  ", etype="person")
    p = project(
        m,
        _SCOPE_LENS,
        Genre.FICTION,
        local_candidates={local_key("Duncan", "PERSON"): 501},
        global_index={},
    )
    assert p.canonical_id == 501 and p.scope == SCOPE_LOCAL


def test_two_lenses_mint_the_same_surface_to_different_local_identities():
    # Fiction isolation, at the resolver level: the lens supplies the candidate map,
    # so the same surface in two lenses resolves to whatever each lens minted.
    m = _mention(surface="Duncan", etype="PERSON")
    p1 = project(
        m,
        Lens("L1", False),
        Genre.FICTION,
        local_candidates={local_key("Duncan", "PERSON"): 601},
        global_index={},
    )
    p2 = project(
        m,
        Lens("L2", False),
        Genre.FICTION,
        local_candidates={local_key("Duncan", "PERSON"): 602},
        global_index={},
    )
    assert p1.canonical_id == 601 and p2.canonical_id == 602
    assert p1.canonical_id != p2.canonical_id


# --- global fallback for a LOCAL-policy mention ---------------------------


def test_local_miss_falls_back_to_global(monkeypatch=None):
    # A fiction-local type whose surface has no minted local identity must not be
    # dropped: it falls back to the global link (invariant 4).
    m = _mention(surface="Napoleon", etype="PERSON")
    p = project(
        m,
        _SCOPE_LENS,
        Genre.FICTION,
        local_candidates={},  # nothing minted for this surface
        global_index={"m1": GlobalLink(canonical_id=517, confidence=0.9)},
    )
    assert p.scope == SCOPE_GLOBAL and p.canonical_id == 517
    assert p.method == METHOD_EXACT_FORM and p.confidence == 0.9


def test_local_miss_and_global_miss_is_explicitly_unresolved():
    m = _mention(surface="Ghïmli", etype="PERSON")
    p = project(m, _SCOPE_LENS, Genre.FICTION, local_candidates={}, global_index={})
    assert p.canonical_id is None
    assert p.scope == SCOPE_GLOBAL  # fell through to global and still missed
    assert p.method == METHOD_UNRESOLVED and p.confidence is None
    assert p.status == STATUS_PROPOSED


# --- GLOBAL-policy mentions (non-fiction, always-global types, unknown) ----


def test_always_global_type_in_fiction_resolves_globally():
    # A DATE in a novel is a real-world referent (genre classification ALWAYS_GLOBAL_TYPES).
    m = _mention(surface="1815", etype="DATE")
    p = project(
        m,
        _SCOPE_LENS,
        Genre.FICTION,
        local_candidates={local_key("1815", "DATE"): 999},  # even if one existed…
        global_index={"m1": GlobalLink(canonical_id=1815, confidence=None)},
    )
    assert p.scope == SCOPE_GLOBAL and p.canonical_id == 1815  # …it is NOT used


def test_non_fiction_person_resolves_globally():
    m = _mention(surface="Paris", etype="GPE")
    p = project(
        m,
        _SCOPE_LENS,
        Genre.NON_FICTION,
        local_candidates={local_key("Paris", "GPE"): 84},  # ignored under non-fiction
        global_index={"m1": GlobalLink(canonical_id=90, confidence=1.0)},
    )
    assert p.scope == SCOPE_GLOBAL and p.canonical_id == 90


def test_same_real_surface_shares_one_global_identity():
    # Non-fiction sharing, at the resolver level: two mentions of Paris carrying the
    # same global link resolve to the same canonical_id regardless of lens.
    ma = Mention("a", "GPE", "Paris")
    mb = Mention("b", "GPE", "Paris")
    gi = {"a": GlobalLink(84, 1.0), "b": GlobalLink(84, 1.0)}
    pa = project(ma, _SCOPE_LENS, Genre.NON_FICTION, local_candidates={}, global_index=gi)
    pb = project(mb, _SCOPE_LENS, Genre.NON_FICTION, local_candidates={}, global_index=gi)
    assert pa.canonical_id == pb.canonical_id == 84


def test_unknown_genre_is_conservatively_global():
    m = _mention(surface="Duncan", etype="PERSON")
    p = project(
        m,
        _SCOPE_LENS,
        Genre.UNKNOWN,
        local_candidates={local_key("Duncan", "PERSON"): 700},  # ignored — not fiction
        global_index={"m1": GlobalLink(1, 1.0)},
    )
    assert p.scope == SCOPE_GLOBAL and p.canonical_id == 1


# --- the global lens forces GLOBAL regardless of genre/type ----------------


def test_global_lens_forces_global_even_for_a_fiction_local_type():
    m = _mention(surface="Duncan", etype="PERSON")
    p = project(
        m,
        _GLOBAL_LENS,
        Genre.FICTION,
        local_candidates={local_key("Duncan", "PERSON"): 42},  # never consulted
        global_index={"m1": GlobalLink(517, 0.5)},
    )
    assert p.lens_id == "lens-global" and p.scope == SCOPE_GLOBAL
    assert p.canonical_id == 517


def test_global_lens_unresolved_when_no_global_link():
    m = _mention(surface="Gandalf", etype="PERSON")
    p = project(m, _GLOBAL_LENS, Genre.FICTION, local_candidates={}, global_index={})
    assert p.scope == SCOPE_GLOBAL and p.canonical_id is None
    assert p.method == METHOD_UNRESOLVED


# --- totality, determinism, order-independence ----------------------------


def test_empty_candidate_sets_never_raise_and_yield_unresolved():
    for genre in Genre:
        for etype in ("PERSON", "DATE", "MADE_UP", ""):
            p = project(
                _mention(etype=etype), _SCOPE_LENS, genre, local_candidates={}, global_index={}
            )
            assert isinstance(p, Projection)
            assert p.status == STATUS_PROPOSED


def test_deterministic_and_order_independent():
    m = _mention(surface="Duncan", etype="PERSON")
    lc = {local_key("Duncan", "PERSON"): 501, local_key("Paul", "PERSON"): 502}
    lc_reordered = {local_key("Paul", "PERSON"): 502, local_key("Duncan", "PERSON"): 501}
    a = project(m, _SCOPE_LENS, Genre.FICTION, local_candidates=lc, global_index={})
    b = project(m, _SCOPE_LENS, Genre.FICTION, local_candidates=lc_reordered, global_index={})
    assert a == b


def test_projection_is_immutable():
    p = project(
        _mention(),
        _SCOPE_LENS,
        Genre.FICTION,
        local_candidates={local_key("Duncan", "PERSON"): 1},
        global_index={},
    )
    import dataclasses

    import pytest

    assert dataclasses.is_dataclass(p)
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.canonical_id = 2  # type: ignore[misc]
