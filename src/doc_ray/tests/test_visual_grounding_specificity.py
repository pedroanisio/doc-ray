"""BL-195: the specificity gate must not mistake stray capitals for entities.

Found 2026-07-29 by repairing the production corpus: seven captions that were
degenerate token soup had been **committed** — i.e. promoted to trusted,
reader-visible output — with grounding scores of 0.98, 0.97 and 0.86.

The cause is in ``_is_proper``: its final clause returns ``index > 0`` for any
token containing an uppercase letter, so a bare ``"A"`` appearing mid-string is
counted as a named entity. Degenerate output made of ``A``, ``The``, ``In`` and
``as`` therefore (a) clears the two-distinct-specific-terms gate and (b) matches
almost any document, because those tokens occur everywhere. The gate scored
noise as near-perfectly grounded.

The captions below are the real committed rows, recovered from the repair.
"""

from __future__ import annotations

import pytest

from doc_ray.application.visual_grounding import grounding_score

#: A page of ordinary prose — every stopword in the soup appears here.
_EVIDENCE = (
    "The diagram shows a network of nodes. A node is drawn as a circle, and the "
    "edges are drawn as lines. In this figure the layout is arranged by hand, as "
    "in the previous chapter."
)

#: Real captions that were committed with grounding 0.98 / 0.97 / 0.86.
_SOUP = [
    ",. , The/. A and A .",
    "A,... A A In as A,.,.",
    ", a,.., . .",
]


@pytest.mark.parametrize("caption", _SOUP)
def test_token_soup_cannot_be_scored_as_specific(caption: str) -> None:
    """Stray capitals are not entities, so the soup has no specific terms."""
    result = grounding_score(caption, _EVIDENCE)
    assert result.specific_terms == 0, (
        f"{caption!r} carries no named entity, numeral or label; scoring it as "
        "specific is what let it auto-commit"
    )
    assert result.insufficient_specificity is True


@pytest.mark.parametrize("caption", _SOUP)
def test_token_soup_cannot_reach_the_auto_commit_threshold(caption: str) -> None:
    """The specificity cap must hold these below 0.8, whatever the overlap."""
    assert grounding_score(caption, _EVIDENCE).score < 0.8


def test_a_single_capital_letter_is_not_a_named_entity() -> None:
    """The precise defect: ``A`` mid-caption was weighted as a proper noun."""
    assert grounding_score("A node A", _EVIDENCE).specific_terms == 0


def test_a_capitalised_stopword_is_not_a_named_entity() -> None:
    """``The``/``In``/``As`` mid-caption are stopwords however they are cased."""
    assert grounding_score("the node In the figure As drawn", _EVIDENCE).specific_terms == 0


def test_real_acronyms_and_numerals_are_still_specific() -> None:
    """The fix must not blunt the signal the gate exists to detect."""
    result = grounding_score("The BEA report on the A330 at gate 12", _EVIDENCE)
    assert result.specific_terms >= 2
    assert result.insufficient_specificity is False


def test_a_genuine_multi_letter_proper_noun_is_still_specific() -> None:
    result = grounding_score("A diagram by Roussel and Hansen of Paris", _EVIDENCE)
    assert result.specific_terms >= 2
    assert result.insufficient_specificity is False
