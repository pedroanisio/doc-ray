"""Prism Tier-3 grounding scorer (proposal §3, Tier 3 — the verification gate).

A pure, deterministic, auditable scorer that measures how much of a VLM caption's
*specific* content (named entities, numerals, distinctive labels) is supported by
the document's own figure text. This is the PALS gate: a generic caption ("a
diagram") carries almost no groundable specificity, so it can never auto-commit —
it is capped and routed to human ``visual_review`` — while a specific caption
that matches specific evidence scores high. The exact matched terms and component
scores are returned so a reviewer sees *why* a caption was trusted or queued.

No model runs here; the scorer is a deterministic function of (caption, evidence)
and is unit-tested directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*")
_STOPWORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "of",
        "to",
        "and",
        "or",
        "in",
        "on",
        "at",
        "for",
        "with",
        "by",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "as",
        "from",
        "into",
        "over",
        "under",
        "above",
        "below",
        "near",
        "about",
        "which",
        "who",
        "whom",
        "whose",
        "then",
        "than",
        "so",
        "such",
        "not",
        "no",
        "yes",
        "can",
        "could",
        "may",
        "might",
        "will",
        "would",
        "shall",
        "should",
        "do",
        "does",
        "did",
        "has",
        "have",
        "had",
    ]
)
#: A caption needs at least this many distinct high-specificity terms before its
#: grounding can clear the auto-commit bar — otherwise it is contentless and is
#: capped so it always routes to review.
_MIN_SPECIFIC_TERMS = 2
_INSUFFICIENT_SPECIFICITY_CAP = 0.5


def _weight(token: str) -> float:
    """Specificity weight: numerals highest, distinctive words high, stopwords ~0."""
    if any(c.isdigit() for c in token):
        return 3.0
    if len(token) >= 4 and token not in _STOPWORDS:
        return 1.0
    return 0.15


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


@dataclass(frozen=True)
class GroundingResult:
    """An auditable grounding verdict for one caption against its figure evidence."""

    score: float
    caption_terms: int
    specific_terms: int
    matched_terms: tuple[str, ...]
    insufficient_specificity: bool


def grounding_score(caption: str, evidence_text: str) -> GroundingResult:
    """Score how well *caption* is grounded in *evidence_text* (0..1).

    ``score`` is the specificity-weighted fraction of the caption's terms found in
    the evidence. A caption with fewer than ``_MIN_SPECIFIC_TERMS`` distinct
    high-specificity terms is flagged ``insufficient_specificity`` and its score
    is capped at ``_INSUFFICIENT_SPECIFICITY_CAP`` so it cannot auto-commit.
    """
    caption_tokens = _tokens(caption)
    evidence = set(_tokens(evidence_text))
    if not caption_tokens:
        return GroundingResult(0.0, 0, 0, (), True)

    total_weight = sum(_weight(t) for t in caption_tokens)
    matched = [t for t in caption_tokens if t in evidence]
    matched_weight = sum(_weight(t) for t in matched)
    raw = matched_weight / total_weight if total_weight else 0.0

    specific_terms = {t for t in caption_tokens if _weight(t) >= 1.0}
    insufficient = len(specific_terms) < _MIN_SPECIFIC_TERMS
    score = min(raw, _INSUFFICIENT_SPECIFICITY_CAP) if insufficient else raw

    # Deterministic, de-duplicated matched terms (specific first, then alnum order).
    matched_unique = sorted(set(matched), key=lambda t: (-_weight(t), t))
    return GroundingResult(
        score=round(score, 6),
        caption_terms=len(caption_tokens),
        specific_terms=len(specific_terms),
        matched_terms=tuple(matched_unique),
        insufficient_specificity=insufficient,
    )
