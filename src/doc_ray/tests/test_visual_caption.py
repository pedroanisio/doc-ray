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


def test_generic_caption_routes_to_review() -> None:
    decision = gate_caption(VLMOutput(caption="a diagram of an aircraft"), _EVIDENCE, threshold=0.8)
    assert decision.status == STATUS_REVIEW  # insufficient specificity, capped


def test_specific_but_unsupported_caption_routes_to_review() -> None:
    decision = gate_caption(
        VLMOutput(caption="Boeing 747 refuelling at Denver gate 12"), _EVIDENCE, threshold=0.8
    )
    assert decision.status == STATUS_REVIEW  # specific, but not in the evidence


def test_empty_caption_is_rejected() -> None:
    assert gate_caption(VLMOutput(caption="  "), _EVIDENCE).status == STATUS_REJECTED
