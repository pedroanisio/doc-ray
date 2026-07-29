"""BL-196: model scaffolding must never reach the human review queue.

The Tier-3 VLM emits its reasoning block and its chat-template sentinels inline
with the caption. Persisted verbatim, those rows reach an operator as
"untrusted caption proposals" that carry no information and cannot be judged —
spending the scarcest resource in the system on strings a regular expression can
reject.

The fixtures below are real output observed on the production corpus
(2026-07-29), not synthetic strings: 364 rows begin with a truncated ``<th``,
118 carry a ``</think>``, 26 carry chat-template sentinels and 48 carry a U+FFFD
replacement character.
"""

from __future__ import annotations

import pytest

from doc_ray.application.visual_caption import gate_caption
from doc_ray.domain.caption_text import is_structurally_invalid, sanitize_caption
from doc_ray.ports.vlm import VLMOutput

# A complete reasoning block wrapping the real caption.
_WITH_THINK = (
    "<think>The image seems to show a diagram. Let me consider the "
    "layout.</think> The image is a black and white diagram illustrating the "
    "spatial layout of a mirrorspace installation."
)
# Observed shape: the opening tag is truncated to `<th`, the block degenerates
# into token soup, and only the trailing `</think>` marks where prose resumes.
_TRUNCATED_THINK = (
    "<th The . </think' and.. Gen thorough and, a,. The for indeed. The, Two,,/, "
    "and, and,, ,\\ . The and,. Seven The A to Gen Two,, Gen . ,-,, �, The A A "
    "A,,,., H a, Positive A.,,.... A A., Two . A,,, the. In,> </think> The image "
    "depicts a textual description of a design process related to architecture."
)
# Observed shape: chat-template sentinels leak in at the tail.
_WITH_SENTINELS = (
    "The diagram includes a spatial layout with two video captures and two "
    "displays, as well as a QR code.<|im_end|> <|im_start|>"
)
# Observed verbatim: the entire stored caption.
_DEGENERATE = "<th a ,"

_CLEAN = (
    "The image is a black and white diagram illustrating the spatial layout and "
    "interactions of a mirrorspace installation by Roussel, Evans & Hansen."
)


def test_a_complete_reasoning_block_is_removed() -> None:
    assert sanitize_caption(_WITH_THINK).startswith("The image is a black and white diagram")
    assert "<think>" not in sanitize_caption(_WITH_THINK)
    assert "</think>" not in sanitize_caption(_WITH_THINK)


def test_a_truncated_reasoning_block_is_removed_up_to_where_prose_resumes() -> None:
    """The opening tag is mangled, so recovery must anchor on the closing tag."""
    cleaned = sanitize_caption(_TRUNCATED_THINK)
    assert cleaned == (
        "The image depicts a textual description of a design process related to architecture."
    )
    assert "Gen thorough" not in cleaned


def test_chat_template_sentinels_are_removed() -> None:
    cleaned = sanitize_caption(_WITH_SENTINELS)
    assert "<|im_end|>" not in cleaned
    assert "<|im_start|>" not in cleaned
    assert cleaned.endswith("a QR code.")


def test_replacement_characters_are_removed() -> None:
    assert "�" not in sanitize_caption("A diagram � of two boxes joined by a line.")


def test_a_clean_caption_is_returned_unchanged() -> None:
    """Sanitisation must be a no-op on well-formed output — 93.7% of the corpus."""
    assert sanitize_caption(_CLEAN) == _CLEAN


def test_whitespace_is_normalised_not_merely_stripped() -> None:
    assert sanitize_caption("  A   diagram\n\nof  two boxes.  ") == "A diagram of two boxes."


@pytest.mark.parametrize("raw", ["", "   ", _DEGENERATE, "<think>only reasoning</think>"])
def test_output_with_nothing_left_after_cleaning_is_structurally_invalid(raw: str) -> None:
    assert is_structurally_invalid(sanitize_caption(raw))


@pytest.mark.parametrize(
    "caption",
    [
        "a diagram of an aircraft",
        "Boeing 747 refuelling at Denver gate 12",
        "Two boxes joined by a line.",
    ],
)
def test_short_but_real_captions_are_not_structurally_invalid(caption: str) -> None:
    """Short is not the same as unreviewable; a char floor got this wrong."""
    assert not is_structurally_invalid(sanitize_caption(caption))


def test_gate_rejects_structurally_invalid_output_instead_of_queueing_it() -> None:
    """A human decision on token soup is wasted; reject it by rule."""
    decision = gate_caption(VLMOutput(caption=_DEGENERATE), "a diagram of two boxes")
    assert decision.status == "rejected"


def test_gate_sanitises_before_grounding_so_scaffolding_cannot_score() -> None:
    """Leaked reasoning text must not be scored as if it were the caption."""
    evidence = "The image depicts a textual description of a design process for architecture."
    dirty = gate_caption(VLMOutput(caption=_TRUNCATED_THINK), evidence)
    clean = gate_caption(
        VLMOutput(
            caption=(
                "The image depicts a textual description of a design process "
                "related to architecture."
            )
        ),
        evidence,
    )
    assert dirty.status == clean.status
    assert dirty.grounding.score == pytest.approx(clean.grounding.score)


def test_a_legitimate_low_grounding_caption_still_reaches_review() -> None:
    """Rejection is for structural invalidity only — it must not swallow the gate."""
    decision = gate_caption(VLMOutput(caption=_CLEAN), "an unrelated page of prose about tax law")
    assert decision.status == "review"


def test_the_decision_carries_the_sanitised_caption_for_persistence() -> None:
    """Stored text and scored text must be the same string.

    Persisting the raw output while scoring the cleaned one would put scaffolding
    back in front of the operator and make the recorded grounding score describe
    a string that is not in the row.
    """
    decision = gate_caption(VLMOutput(caption=_WITH_THINK), "a mirrorspace installation diagram")
    assert "<think>" not in decision.caption
    assert decision.caption.startswith("The image is a black and white diagram")


# Further real shapes found by running the sanitiser over all 386 corrupted
# production rows (2026-07-29) — the closing tag is not always at the head.
_MID_STRING_CLOSE = (
    'The image features a solid black background with the text "upGrad" in bold. '
    '</think> The image features a solid black background with the text "upGrad".'
)
_TOOL_CALL_LEAK = (
    '{"name": "describe_image", "arguments": {"image_url></think> The image is a '
    "detailed flowchart with a white background and three labelled boxes."
)
_BRACKET_SENTINEL = (
    "[im_end]</think> The image is a simple flowchart with a white background and "
    "no additional elements."
)


def test_a_closing_tag_anywhere_discards_everything_before_it() -> None:
    """Reasoning can precede the caption without a well-formed opening tag."""
    cleaned = sanitize_caption(_MID_STRING_CLOSE)
    assert cleaned.startswith("The image features a solid black background")
    assert "</think>" not in cleaned
    assert cleaned.count("upGrad") == 1


def test_leaked_tool_call_json_before_the_caption_is_discarded() -> None:
    cleaned = sanitize_caption(_TOOL_CALL_LEAK)
    assert cleaned.startswith("The image is a detailed flowchart")
    assert "describe_image" not in cleaned


def test_bracket_form_chat_sentinels_are_removed() -> None:
    cleaned = sanitize_caption(_BRACKET_SENTINEL)
    assert "[im_end]" not in cleaned
    assert cleaned.startswith("The image is a simple flowchart")
