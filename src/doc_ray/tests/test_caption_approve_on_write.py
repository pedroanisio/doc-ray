"""BL-201: captions are approved on write and retracted on sight.

The pre-publication gate did not work at corpus scale. Of 6,122 proposals only
21 were ever committed, because the auto-commit threshold was unreachable and
5,851 items is not a queue any human clears. The result was not caution — it was
a feature that never shipped, with the whole corpus's captions invisible.

The flow now inverts: a caption that is structurally valid is committed on write
and appears in the reader, where a human who can see the image retracts it if it
is wrong. Verification does not disappear, it moves — and it must stay auditable,
which is what the retraction columns are for.

The floor is configurable so the trade can be tightened later without another
inversion: raise ``DOCRAY_CAPTION_APPROVE_FLOOR`` and captions below it go back
to the review queue instead of the reader.
"""

from __future__ import annotations

import pytest

from doc_ray.application.visual_caption import (
    STATUS_COMMITTED,
    STATUS_REJECTED,
    STATUS_REVIEW,
    gate_caption,
)
from doc_ray.ports.vlm import VLMOutput

_EVIDENCE = "The chart plots revenue against quarter for the Boeing 747 programme."
_GOOD = "A line chart of revenue by quarter for the Boeing 747 programme."
_WEAK = "A picture of a thing that is quite hard to describe in any detail."


def test_a_valid_caption_is_committed_on_write_by_default() -> None:
    """The default no longer withholds; it publishes and invites retraction."""
    assert gate_caption(VLMOutput(caption=_GOOD), _EVIDENCE).status == STATUS_COMMITTED


def test_even_a_weakly_grounded_caption_is_committed_at_the_default_floor() -> None:
    """Floor 0.0 means grounding informs the reader rather than gating it."""
    assert gate_caption(VLMOutput(caption=_WEAK), _EVIDENCE).status == STATUS_COMMITTED


def test_structural_invalidity_still_rejects_outright() -> None:
    """Inverting the gate must not let scaffolding reach the reader (BL-196)."""
    assert gate_caption(VLMOutput(caption="<th a ,"), _EVIDENCE).status == STATUS_REJECTED


def test_raising_the_floor_routes_weak_captions_back_to_review() -> None:
    """The trade is tunable: the queue is still there when it is wanted."""
    decision = gate_caption(VLMOutput(caption=_WEAK), _EVIDENCE, approve_floor=0.9)
    assert decision.status == STATUS_REVIEW


def test_a_caption_at_the_floor_is_committed_not_queued() -> None:
    """The boundary is inclusive, so a floor of 0.0 can never queue anything."""
    decision = gate_caption(VLMOutput(caption=_GOOD), _EVIDENCE, approve_floor=0.0)
    assert decision.status == STATUS_COMMITTED


def test_the_grounding_score_is_still_computed_when_it_no_longer_gates() -> None:
    """The reader shows the score beside the claim, so it must still be real."""
    decision = gate_caption(VLMOutput(caption=_GOOD), _EVIDENCE)
    assert 0.0 < decision.grounding.score <= 1.0
    assert decision.grounding.matched_terms


@pytest.mark.parametrize("floor", [-0.1, 1.5])
def test_an_out_of_range_floor_is_rejected_rather_than_silently_clamped(floor: float) -> None:
    with pytest.raises(ValueError, match="approve_floor"):
        gate_caption(VLMOutput(caption=_GOOD), _EVIDENCE, approve_floor=floor)
