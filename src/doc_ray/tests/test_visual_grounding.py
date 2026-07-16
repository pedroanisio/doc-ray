"""The Tier-3 grounding scorer: specific captions ground, generic ones can't.

Locks the PALS gate — a generic caption must never be able to auto-commit, and a
specific caption is trusted only when its specific terms are actually in the
document's figure text.
"""

from __future__ import annotations

from doc_ray.application.visual_grounding import grounding_score

_EVIDENCE = "Figure 3: Airbus A330 altitude 38000 feet over the Atlantic during the stall."


def test_specific_caption_matching_evidence_scores_high() -> None:
    r = grounding_score("Airbus A330 at 38000 feet over the Atlantic", _EVIDENCE)
    assert r.score >= 0.8
    assert not r.insufficient_specificity
    assert "38000" in r.matched_terms and "airbus" in r.matched_terms


def test_generic_caption_is_capped_and_routes_to_review() -> None:
    # "a diagram" has no groundable specificity; it must never auto-commit.
    r = grounding_score("a diagram of it", _EVIDENCE)
    assert r.insufficient_specificity
    assert r.score <= 0.5


def test_specific_caption_absent_from_evidence_scores_low() -> None:
    r = grounding_score("Boeing 747 refuelling at Denver airport gate 12", _EVIDENCE)
    assert r.score < 0.5  # its specific terms are not in the evidence
    assert not r.insufficient_specificity  # it *is* specific, just unsupported


def test_numerals_are_weighted_and_audited() -> None:
    with_num = grounding_score("altitude 38000 feet Atlantic", _EVIDENCE)
    without_num = grounding_score("altitude cruise feet Atlantic", _EVIDENCE)
    assert with_num.score > without_num.score
    assert "38000" in with_num.matched_terms


def test_empty_caption_is_zero() -> None:
    r = grounding_score("", _EVIDENCE)
    assert r.score == 0.0 and r.insufficient_specificity


def test_result_is_deterministic() -> None:
    assert grounding_score("Airbus A330 38000 feet", _EVIDENCE) == grounding_score(
        "Airbus A330 38000 feet", _EVIDENCE
    )
