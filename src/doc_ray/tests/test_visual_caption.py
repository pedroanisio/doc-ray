"""The Tier-3 caption gate: commit only grounded+specific captions, else review.

Locks the PALS decision boundary at the pure-function level (no DB, no model):
a specific caption supported by the figure text commits; a generic one is routed
to review; an empty one is rejected.
"""

from __future__ import annotations

from doc_ray.application.visual_caption import (
    STATUS_COMMITTED,
    STATUS_REJECTED,
    STATUS_REVIEW,
    gate_caption,
)
from doc_ray.ports.vlm import VLMOutput

_EVIDENCE = "Figure 3: Airbus A330 altitude 38000 feet over the Atlantic during the stall."


def test_grounded_specific_caption_commits() -> None:
    decision = gate_caption(
        VLMOutput(caption="Airbus A330 at 38000 feet over the Atlantic"), _EVIDENCE, threshold=0.8
    )
    assert decision.status == STATUS_COMMITTED
    assert decision.grounding.score >= 0.8


def test_generic_caption_is_published_and_left_to_retraction() -> None:
    """Inverted by BL-201: a generic caption publishes with a visible low score.

    Withholding it produced no review, only invisibility. It now appears in the
    reader labelled with its grounding score, where a human who can see the image
    retracts it if it is wrong.
    """
    decision = gate_caption(VLMOutput(caption="a diagram of an aircraft"), _EVIDENCE, threshold=0.8)
    assert decision.status == STATUS_COMMITTED
    assert decision.grounding.insufficient_specificity is True


def test_generic_caption_routes_to_review_when_a_floor_is_set() -> None:
    """Raising the floor restores the pre-publication queue for weak captions."""
    decision = gate_caption(
        VLMOutput(caption="a diagram of an aircraft"), _EVIDENCE, approve_floor=0.9
    )
    assert decision.status == STATUS_REVIEW


def test_specific_but_unsupported_caption_is_published_with_its_low_score() -> None:
    """Specific-but-ungrounded is exactly what a reader can adjudicate on sight."""
    decision = gate_caption(
        VLMOutput(caption="Boeing 747 refuelling at Denver gate 12"), _EVIDENCE, threshold=0.8
    )
    assert decision.status == STATUS_COMMITTED
    assert decision.grounding.score < 0.8  # visibly unsupported, and shown as such


def test_empty_caption_is_rejected() -> None:
    assert gate_caption(VLMOutput(caption="  "), _EVIDENCE).status == STATUS_REJECTED
