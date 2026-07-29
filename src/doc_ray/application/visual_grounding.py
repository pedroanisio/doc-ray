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


def _has_digit(token: str) -> bool:
    return any(c.isdigit() for c in token)


def _is_proper(token: str, index: int) -> bool:
    """True for entity-like tokens: acronyms, or capitalized words not sentence-initial.

    This is the *specificity* signal (numerals + named entities/labels), the thing
    the proposal says must carry grounding. A plain sentence-initial capital
    ("Picture …") is NOT counted — only mid-sentence proper nouns, ALL-CAPS
    acronyms, or multi-capital tokens — so a generic caption cannot clear the gate.
    """
    if not any(c.isupper() for c in token):
        return False
    if token.isupper() and len(token) >= 2:  # acronym: BEA, PDF
        return True
    if sum(1 for c in token if c.isupper()) > 1:  # e.g. A330, iPhone
        return True
    # A single character is never a distinctive label. Degenerate VLM output is
    # dense in bare "A", and treating each one as a named entity is what let
    # token soup clear the specificity gate and auto-commit with a 0.98 score
    # (BL-195, seven such captions found committed on 2026-07-29).
    if len(token) < 2:
        return False
    # A capitalised stopword is still a stopword. "The", "In" and "As" appear
    # mid-caption in degenerate output and match any document's prose, so
    # counting them as entities makes noise look grounded.
    if token.lower() in _STOPWORDS:
        return False
    return index > 0  # a capitalized word that isn't the first token


def _is_specific(token: str, index: int) -> bool:
    return _has_digit(token) or _is_proper(token, index)


def _weight(token: str, index: int) -> float:
    """Specificity weight for the overlap score (specific terms dominate)."""
    if _has_digit(token):
        return 3.0
    if _is_proper(token, index):
        return 2.0
    if len(token) >= 4 and token.lower() not in _STOPWORDS:
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
    original = _TOKEN_RE.findall(caption)
    if not original:
        return GroundingResult(0.0, 0, 0, (), True)
    lower = [t.lower() for t in original]
    weights = [_weight(t, i) for i, t in enumerate(original)]
    evidence = set(_tokens(evidence_text))

    total_weight = sum(weights)
    matched_weight = sum(w for t, w in zip(lower, weights, strict=True) if t in evidence)
    raw = matched_weight / total_weight if total_weight else 0.0

    # Specificity gate: distinct numerals/entities/labels the caption actually
    # carries. Generic captions have none, so they can never auto-commit.
    specific = {t.lower() for i, t in enumerate(original) if _is_specific(t, i)}
    insufficient = len(specific) < _MIN_SPECIFIC_TERMS
    score = min(raw, _INSUFFICIENT_SPECIFICITY_CAP) if insufficient else raw

    matched_terms = {t for i, t in enumerate(lower) if t in evidence}
    matched_unique = sorted(matched_terms, key=lambda t: t)
    return GroundingResult(
        score=round(score, 6),
        caption_terms=len(original),
        specific_terms=len(specific),
        matched_terms=tuple(matched_unique),
        insufficient_specificity=insufficient,
    )
