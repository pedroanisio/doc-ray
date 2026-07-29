"""Strip model scaffolding out of a Tier-3 caption before anything trusts it.

A reasoning-capable VLM emits its deliberation, and sometimes its chat-template
sentinels, inline with the answer. Persisted verbatim that text reaches an
operator as a reviewable proposal, and — worse — is scored by the grounding
metric as if it were the caption, so scaffolding drags the score of an otherwise
sound caption toward zero.

This module is pure text handling with no I/O, so the rules are testable against
real captured output rather than against a mock. It is deliberately conservative:
it removes only constructs that are unambiguously machine scaffolding, and is a
no-op on well-formed prose (93.7% of the production corpus measured 2026-07-29).
"""

from __future__ import annotations

import re

#: Fewest word-like tokens a caption can carry and still be adjudicable.
#:
#: Deliberately a *content* test, not a length one. A character floor drawn from
#: the corpus distribution (p05 = 141 chars) would have rejected legitimate short
#: captions such as "a diagram of an aircraft" (24 chars) and "Boeing 747
#: refuelling at Denver gate 12" (39) — both of which the gate is required to
#: route to review, not reject. Counting words separates "short" from "not a
#: caption": the observed degenerate row ``<th a ,`` yields one token, while the
#: shortest legitimate captions in the suite yield four or more.
MIN_CAPTION_WORDS = 3

#: A word-like token: two or more letters. Digits and punctuation do not count
#: toward the floor, so "747 12 ," cannot pass as content.
_WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)

#: A complete reasoning block. Non-greedy so several blocks each collapse.
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

#: The reliable landmark is the CLOSING tag, wherever it falls. The opening tag
#: is frequently truncated (``<th``), absent entirely, or replaced by leaked
#: tool-call JSON, so anchoring on the opening tag misses most real cases;
#: anchoring on the last closing tag handles all of them uniformly. Everything
#: before the final ``</think>`` is deliberation, never caption.
_CLOSE_TAG = re.compile(r"^.*</think\s*>", re.DOTALL | re.IGNORECASE)

#: A partial opening tag with no closing tag anywhere: nothing is recoverable.
_BARE_LEAD = re.compile(r"^\s*<think\b.*", re.DOTALL | re.IGNORECASE)

#: A truncated tag fragment left at the head once the blocks above are gone —
#: the observed ``<th a ,`` shape. A caption never legitimately opens with '<',
#: so removing a short leading pseudo-tag cannot eat real prose.
_PARTIAL_TAG_LEAD = re.compile(r"^\s*</?[a-z|][^>]{0,20}>?", re.IGNORECASE)

#: Chat-template sentinels. Both the canonical ``<|im_end|>`` form and the
#: degraded ``[im_end]`` form appear in the corpus, so the delimiters are
#: optional — but the NAME is an explicit allowlist, because a generic
#: bracket-stripping rule would eat legitimate bracketed prose.
_SENTINEL = re.compile(
    r"[<\[]\s*\|?\s*(?:im_start|im_end|endoftext|eot_id|start_header_id|end_header_id)"
    r"\s*\|?\s*[>\]]",
    re.IGNORECASE,
)

#: U+FFFD marks bytes the decoder could not represent; it is never caption text.
_REPLACEMENT = "�"


def sanitize_caption(raw: str | None) -> str:
    """Return *raw* with model scaffolding removed and whitespace normalised.

    Args:
        raw: The caption exactly as the VLM emitted it, or ``None``.

    Returns:
        The caption text alone. Empty when nothing survives — the caller decides
        what an empty result means, so this function never raises.
    """
    text = raw or ""
    text = _THINK_BLOCK.sub(" ", text)
    text = _CLOSE_TAG.sub(" ", text)
    text = _BARE_LEAD.sub(" ", text)
    text = _SENTINEL.sub(" ", text)
    text = text.replace(_REPLACEMENT, " ")
    text = _PARTIAL_TAG_LEAD.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_structurally_invalid(caption: str) -> bool:
    """Report whether *caption* carries no reviewable content.

    Structural invalidity is a property of the string, not a judgement about
    whether the caption is *true* — that is what grounding measures. Keeping the
    two separate is what lets this be applied by rule without weakening the gate.

    Args:
        caption: An already-sanitised caption.

    Returns:
        ``True`` when the string carries fewer than :data:`MIN_CAPTION_WORDS`
        word-like tokens — i.e. it is not a caption at all, however short a real
        caption may legitimately be.
    """
    return len(_WORD.findall(caption)) < MIN_CAPTION_WORDS
