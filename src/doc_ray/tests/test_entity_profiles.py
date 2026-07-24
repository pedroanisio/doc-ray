"""Entity reconciliation for the read surface (BL-146, P1 item 6).

Raw NER splits one real-world entity across types and surface spellings. Measured
in the live corpus: ``Arrakis`` is tagged LOC 225×, GPE 150×, PERSON 95×, NORP
76× — four rows an agent must reconcile by hand before it can say anything about
a place. ``entity_frequency`` returns exactly those raw rows.

:mod:`doc_ray.domain.entity_profiles` folds them into one row per normalized
surface form, carrying the *measured* type spread and a consistency ratio.
PALS's Law at the surface: the reconciliation is presented as what it is —
arithmetic over untrusted ML output, with the disagreement visible — never as a
settled identity. The function is pure, so this suite needs no database.
"""

from __future__ import annotations

import pytest

from doc_ray.domain.entity_profiles import reconcile


def _row(text: str, entity_type: str, count: int) -> dict[str, object]:
    return {"text": text, "entity_type": entity_type, "count": count}


#: The real Arrakis split, straight from the production corpus.
ARRAKIS = [
    _row("Arrakis", "LOC", 225),
    _row("Arrakis", "GPE", 150),
    _row("Arrakis", "PERSON", 95),
    _row("Arrakis", "NORP", 76),
]


# --------------------------------------------------------------------------
# 1. The headline behaviour: four raw rows collapse to one honest profile.
# --------------------------------------------------------------------------


def test_the_arrakis_split_becomes_one_profile_with_the_spread_visible():
    (profile,) = reconcile(ARRAKIS)
    assert profile.surface == "Arrakis"
    assert profile.mentions == 546  # 225 + 150 + 95 + 76
    assert profile.dominant_type == "LOC"
    # The disagreement is not hidden — it is the payload.
    assert [(t.entity_type, t.count) for t in profile.type_spread] == [
        ("LOC", 225),
        ("GPE", 150),
        ("PERSON", 95),
        ("NORP", 76),
    ]


def test_type_consistency_is_the_measured_dominant_share_not_a_model_score():
    (profile,) = reconcile(ARRAKIS)
    assert profile.type_consistency == pytest.approx(225 / 546, abs=1e-6)
    # Well under 1.0 — this row is exactly the "do not trust the type" signal.
    assert profile.type_consistency < 0.5


def test_an_unambiguous_entity_scores_full_consistency():
    (profile,) = reconcile([_row("Duncan Idaho", "PERSON", 40)])
    assert profile.type_consistency == 1.0
    assert profile.dominant_type == "PERSON"
    assert profile.mentions == 40


# --------------------------------------------------------------------------
# 2. Grouping key — surface variants fold, distinct entities do not.
# --------------------------------------------------------------------------


def test_case_and_accent_variants_fold_onto_one_profile():
    # form_norm() is the same key form_index uses, so this grouping agrees with
    # the canonical registry rather than inventing a second notion of sameness.
    profiles = reconcile(
        [_row("ARRAKIS", "LOC", 10), _row("Arrakis", "LOC", 5), _row("Arrákis", "GPE", 2)]
    )
    assert len(profiles) == 1
    assert profiles[0].mentions == 17
    assert set(profiles[0].surface_forms) == {"ARRAKIS", "Arrakis", "Arrákis"}


def test_the_reported_surface_is_the_most_frequent_spelling():
    profiles = reconcile([_row("ARRAKIS", "LOC", 3), _row("Arrakis", "LOC", 40)])
    assert profiles[0].surface == "Arrakis"


def test_distinct_entities_are_never_merged():
    # "Duncan" and "Duncan Idaho" are a coreference question, not a normalization
    # one. Merging them would be fabrication, so they stay two profiles.
    profiles = reconcile([_row("Duncan", "PERSON", 1106), _row("Duncan Idaho", "PERSON", 40)])
    assert len(profiles) == 2
    assert {p.surface for p in profiles} == {"Duncan", "Duncan Idaho"}


# --------------------------------------------------------------------------
# 3. Ordering, limits, and edges.
# --------------------------------------------------------------------------


def test_profiles_are_ordered_by_total_mentions_descending():
    # Real counts: Paul 2473, Teg 1268, Arrakis 546 (225+150+95+76). The grouped
    # total is what ranks an entity, so a split entity is not pushed down the list
    # by having been split.
    profiles = reconcile([_row("Teg", "PERSON", 1268), *ARRAKIS, _row("Paul", "PERSON", 2473)])
    assert [p.surface for p in profiles] == ["Paul", "Teg", "Arrakis"]
    assert [p.mentions for p in profiles] == [2473, 1268, 546]


def test_ties_break_deterministically_on_the_normalized_key():
    profiles = reconcile([_row("beta", "ORG", 7), _row("alpha", "ORG", 7)])
    assert [p.surface for p in profiles] == ["alpha", "beta"]


def test_limit_applies_after_grouping_not_before():
    # Truncating raw rows first would drop the GPE half of Arrakis and report a
    # smaller, wrong total. The limit must bound *profiles*.
    profiles = reconcile([*ARRAKIS, _row("Teg", "PERSON", 1268)], limit=1)
    assert len(profiles) == 1
    assert profiles[0].surface == "Teg"  # 1268 > 546
    assert reconcile([*ARRAKIS, _row("Teg", "PERSON", 1268)], limit=2)[1].mentions == 546


def test_empty_input_yields_no_profiles():
    assert reconcile([]) == []


def test_blank_and_zero_count_rows_are_ignored_not_crashed_on():
    profiles = reconcile([_row("", "PERSON", 5), _row("   ", "ORG", 2), _row("Real", "PERSON", 1)])
    assert [p.surface for p in profiles] == ["Real"]


def test_missing_count_is_treated_as_zero_and_the_entity_still_appears():
    profiles = reconcile([{"text": "Ghola", "entity_type": "PERSON"}])
    assert profiles[0].mentions == 0
    assert profiles[0].type_consistency == 0.0
