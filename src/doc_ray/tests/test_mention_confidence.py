"""Block G mention-confidence scoring: pure, explainable review ranking.

The scorer combines corpus facts already available to Doc-Ray and must never
present their arithmetic as a calibrated model probability.  These tests pin
each heuristic independently, their composition, deterministic ordering, and
the boundary behavior used by the ranked review surface.
"""

from __future__ import annotations

import pytest

from doc_ray.domain.mention_confidence import (
    ALL_CAPS_PENALTY,
    CROSS_SENTENCE_PENALTY,
    PERSON_DIGIT_PENALTY,
    RARITY_MAX_PENALTY,
    SINGLE_CHARACTER_PENALTY,
    TYPE_DISAGREEMENT_MAX_PENALTY,
    UNRESOLVED_PENALTY,
    Signal,
    rarity_signal,
    registry_novelty_signal,
    score,
    sentence_boundary_signal,
    type_disagreement_signal,
)
from doc_ray.domain.ner_corrections import Mention


def _mention(surface: str = "Duncan Idaho", entity_type: str = "PERSON") -> Mention:
    return Mention(
        char_start=10, char_end=10 + len(surface), entity_type=entity_type, surface=surface
    )


def test_type_disagreement_uses_the_shared_dominant_type_ratio():
    signal = type_disagreement_signal({"LOC": 4, "GPE": 6})
    assert signal is not None
    assert signal.penalty == pytest.approx(TYPE_DISAGREEMENT_MAX_PENALTY * 0.4)
    confidence, reasons = score(_mention("Arrakis", "LOC"), [signal])
    assert confidence == pytest.approx(1.0 - signal.penalty)
    assert reasons == ["entity type disagrees across 40% of its mentions"]


def test_consistent_type_adds_no_disagreement_penalty():
    assert type_disagreement_signal({"PERSON": 40}) is None


def test_unresolved_surface_has_an_explainable_registry_penalty():
    signal = registry_novelty_signal(resolved=False)
    assert signal == Signal(
        name="registry_novelty",
        penalty=UNRESOLVED_PENALTY,
        reason="surface is unresolved in the canonical registry",
    )
    assert registry_novelty_signal(resolved=True) is None


def test_single_occurrence_receives_the_maximum_rarity_penalty():
    signal = rarity_signal(occurrence_count=1)
    assert signal is not None
    assert signal.penalty == RARITY_MAX_PENALTY
    assert signal.reason == "surface occurs only once in the corpus"


def test_frequent_surface_has_no_rarity_penalty():
    assert rarity_signal(occurrence_count=200) is None


def test_zero_and_low_counts_remain_explicit_and_monotonic():
    zero = rarity_signal(occurrence_count=0)
    two = rarity_signal(occurrence_count=2)
    assert zero is not None and two is not None
    assert zero.reason == "surface has no counted corpus occurrences"
    assert two.reason == "surface is rare (2 corpus occurrences)"
    assert zero.penalty > two.penalty > 0.0


def test_negative_occurrence_count_is_rejected():
    with pytest.raises(ValueError, match="occurrence_count"):
        rarity_signal(occurrence_count=-1)


@pytest.mark.parametrize(
    ("mention", "expected_penalty", "reason"),
    [
        (_mention("A"), SINGLE_CHARACTER_PENALTY, "surface is a single character"),
        (_mention("DUNCAN"), ALL_CAPS_PENALTY, "surface is all uppercase"),
        (
            _mention("Duncan 2"),
            PERSON_DIGIT_PENALTY,
            "PERSON surface contains a digit",
        ),
    ],
)
def test_shape_anomalies_are_derived_from_the_mention(mention, expected_penalty, reason):
    confidence, reasons = score(mention, [])
    assert confidence == pytest.approx(1.0 - expected_penalty)
    assert reasons == [reason]


def test_non_person_digits_do_not_trigger_the_person_shape_signal():
    confidence, reasons = score(_mention("Area 51", "FAC"), [])
    assert confidence == 1.0
    assert reasons == ["no heuristic uncertainty signals detected"]


def test_sentence_boundary_crossing_has_an_explainable_penalty():
    signal = sentence_boundary_signal(crosses_boundary=True)
    assert signal is not None
    assert signal.penalty == CROSS_SENTENCE_PENALTY
    assert signal.reason == "span crosses a sentence boundary"
    assert sentence_boundary_signal(crosses_boundary=False) is None


def test_combined_signals_rank_a_known_bad_mention_before_a_known_good_one():
    bad_signals = [
        type_disagreement_signal({"LOC": 4, "GPE": 3, "PERSON": 2, "NORP": 1}),
        registry_novelty_signal(resolved=False),
        rarity_signal(occurrence_count=1),
    ]
    good_signals = [
        type_disagreement_signal({"PERSON": 200}),
        registry_novelty_signal(resolved=True),
        rarity_signal(occurrence_count=200),
    ]
    bad_confidence, bad_reasons = score(_mention("Arrakis", "LOC"), bad_signals)
    good_confidence, good_reasons = score(_mention(), good_signals)
    assert bad_confidence < good_confidence
    assert len(bad_reasons) == 3
    assert good_confidence == 1.0
    assert good_reasons == ["no heuristic uncertainty signals detected"]


def test_signal_order_never_changes_the_score_or_reason_order():
    signals = [
        registry_novelty_signal(resolved=False),
        rarity_signal(occurrence_count=1),
        type_disagreement_signal({"GPE": 2, "LOC": 1}),
    ]
    forward = score(_mention("Arrakis", "GPE"), signals)
    reverse = score(_mention("Arrakis", "GPE"), reversed(signals))
    assert forward == reverse


def test_duplicate_signal_names_use_the_strongest_observation_once():
    weak = Signal("rarity", 0.05, "surface is uncommon")
    strong = Signal("rarity", 0.20, "surface occurs only once in the corpus")
    confidence, reasons = score(_mention(), [weak, strong, weak])
    assert confidence == pytest.approx(0.8)
    assert reasons == ["surface occurs only once in the corpus"]


def test_score_is_clamped_to_zero_when_penalties_exhaust_confidence():
    confidence, reasons = score(
        _mention(),
        [
            Signal("synthetic_a", 0.8, "first severe signal"),
            Signal("synthetic_b", 0.8, "second severe signal"),
        ],
    )
    assert confidence == 0.0
    assert reasons == ["first severe signal", "second severe signal"]


def test_clean_mention_scores_one_and_still_has_a_human_readable_reason():
    confidence, reasons = score(_mention(), [])
    assert confidence == 1.0
    assert reasons == ["no heuristic uncertainty signals detected"]


@pytest.mark.parametrize("penalty", [-0.01, 1.01])
def test_signal_rejects_out_of_range_penalties(penalty):
    with pytest.raises(ValueError, match="penalty"):
        Signal("invalid", penalty, "invalid signal")


@pytest.mark.parametrize(
    ("name", "reason", "message"),
    [("", "reason", "name"), ("signal", "", "reason")],
)
def test_signal_rejects_blank_identity_or_explanation(name, reason, message):
    with pytest.raises(ValueError, match=message):
        Signal(name, 0.1, reason)
