"""Unit tests for the heuristic genre classifier (issue #6).

No database, no network, no LLM. The classifier reads deterministic, citable
signals (dialogue density, first-person narration, chapter headings vs. an
academic apparatus of abstract/references/citations, and document metadata) and
emits a genre, a confidence, and a rationale. Per PALS's Law it NEVER asserts a
machine guess as confirmed: every machine result is ``status='pending'``, and an
ambiguous document abstains to ``genre='unknown'`` rather than being mislabelled.
"""

from __future__ import annotations

from doc_ray.application.genre_classification import (
    CONFIRM_THRESHOLD,
    METHOD_HEURISTIC,
    classify_genre,
)
from doc_ray.domain.genre_policy import Genre

_NOVEL = """
Chapter One

"You can't be serious," Maria said, her voice trembling. "After everything?"

I had waited my whole life for this moment, and now that it had come I could
barely breathe. "I am," he whispered. "I always was."

She turned away. The rain kept falling against the window, and I remembered the
summer we first met, when everything still felt possible.

Chapter Two

"Where will you go?" I asked. She didn't answer.
"""

_PAPER = """
Abstract

We present a method for estimating corpus-level entity coreference at scale.
Our approach improves precision over prior work (Smith et al., 2019).

1. Introduction

Prior systems [1], [2] rely on global resolution. In this paper we evaluate a
lens-scoped alternative and report results on a held-out set.

2. Methodology

We sampled 10,000 documents and measured F1 against a gold standard.

References

[1] A. Smith et al. Coreference at scale. DOI:10.1000/abcd.
[2] B. Jones. Entity linking. Journal of NLP, 2020.
"""

_AMBIGUOUS = """
The market opened lower today. Analysts noted continued uncertainty, though a
few remained optimistic about the coming quarter. Trading volume was moderate.
The weather remained mild across the region for most of the afternoon.
"""


def test_novel_text_classifies_as_fiction_and_is_confident():
    result = classify_genre(_NOVEL)
    assert result.genre is Genre.FICTION
    assert result.confidence >= CONFIRM_THRESHOLD
    assert result.method == METHOD_HEURISTIC
    # PALS: a machine result is never asserted as confirmed.
    assert result.status == "pending"
    assert result.rationale  # citable signals, not an empty string


def test_paper_text_classifies_as_non_fiction_and_is_confident():
    result = classify_genre(_PAPER)
    assert result.genre is Genre.NON_FICTION
    assert result.confidence >= CONFIRM_THRESHOLD
    assert result.status == "pending"


def test_ambiguous_text_abstains_to_unknown_below_threshold():
    result = classify_genre(_AMBIGUOUS)
    # The load-bearing assertion (D-1): an ambiguous document must NOT be
    # mislabelled — it abstains, below threshold, pending review.
    assert result.confidence < CONFIRM_THRESHOLD
    assert result.genre is Genre.UNKNOWN
    assert result.status == "pending"


def test_metadata_subject_reinforces_fiction():
    result = classify_genre(_NOVEL, metadata={"subject": "Fiction / Literary", "keywords": "novel"})
    assert result.genre is Genre.FICTION
    assert result.confidence >= CONFIRM_THRESHOLD


def test_confidence_is_bounded_and_classifier_is_deterministic():
    for text in (_NOVEL, _PAPER, _AMBIGUOUS):
        a = classify_genre(text)
        b = classify_genre(text)
        assert 0.0 <= a.confidence <= 1.0
        assert (a.genre, a.confidence, a.status) == (b.genre, b.confidence, b.status)


def test_machine_classifier_never_emits_confirmed_status():
    for text in (_NOVEL, _PAPER, _AMBIGUOUS):
        assert classify_genre(text).status == "pending"


def test_empty_text_abstains():
    result = classify_genre("")
    assert result.genre is Genre.UNKNOWN
    assert result.status == "pending"
    assert result.confidence < CONFIRM_THRESHOLD
