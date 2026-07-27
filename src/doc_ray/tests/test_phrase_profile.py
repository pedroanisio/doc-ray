"""Contract tests for the versioned surface-word phrase profile (#44)."""

from __future__ import annotations

import random
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from doc_ray.domain import phrase_profile as pp
from doc_ray.domain import search_terms

REPO_ROOT = Path(__file__).parents[3]


def _hits(text: str, spans: tuple[tuple[int, int], ...]) -> tuple[str, ...]:
    return tuple(text[start:end] for start, end in spans)


@pytest.mark.parametrize(
    ("text", "phrase", "expected"),
    [
        ("Tangram ngram ungrammatical", "ngram", ("ngram",)),
        ("algorithm designed", "algorithm design", ()),
        ("Algorithm Design", "algorithm design", ("Algorithm Design",)),
        ("algorithm  design", "algorithm design", ("algorithm  design",)),
    ],
)
def test_boundary_vectors_match_whole_words_only(
    text: str, phrase: str, expected: tuple[str, ...]
) -> None:
    assert _hits(text, pp.find_occurrences(text, phrase)) == expected


def test_word_spans_follow_the_unicode_digit_and_underscore_policy() -> None:
    text = "éclair ٢٠٢٤ shared_buffers COVID-19"

    assert _hits(text, pp.word_spans(text)) == (
        "éclair",
        "٢٠٢٤",
        "shared_buffers",
        "COVID",
        "19",
    )


def test_phrase_words_are_normalized_without_losing_word_identity() -> None:
    assert pp.phrase_words("  Algorithm SHARED_BUFFERS 2024 ") == (
        "algorithm",
        "shared_buffers",
        "2024",
    )


def test_ascii_and_typographic_apostrophes_are_equivalent_joiners() -> None:
    text = "Hick’s law differs from Hick's law."

    assert _hits(text, pp.find_occurrences(text, "Hick's law")) == (
        "Hick’s law",
        "Hick's law",
    )


@pytest.mark.parametrize("dash", ("-", "–", "—", "‐"))
def test_hyphen_and_dash_variants_preserve_adjacency(dash: str) -> None:
    text = f"well{dash}being"

    assert pp.find_occurrences(text, "well-being") == ((0, len(text)),)


def test_double_quotes_are_boundaries_but_not_internal_joiners() -> None:
    quoted = "“scale model”"

    assert _hits(quoted, pp.find_occurrences(quoted, '"SCALE MODEL"')) == ("scale model",)
    assert pp.find_occurrences("scale “model", "scale model") == ()


def test_whitespace_runs_join_words_but_sentence_punctuation_breaks_them() -> None:
    assert _hits("I am not\t  happy.", pp.find_occurrences("I am not\t  happy.", "not happy")) == (
        "not\t  happy",
    )
    assert pp.find_occurrences("I am not. Happy.", "not happy") == ()
    assert pp.find_occurrences("not happy", "not. happy") == ()


def test_joiner_and_word_character_decisions_cover_bl164_terms() -> None:
    text = "shared_buffers and COVID-19 in 2024"

    assert _hits(text, pp.find_occurrences(text, "shared_buffers")) == ("shared_buffers",)
    assert pp.find_occurrences(text, "shared buffers") == ()
    assert _hits(text, pp.find_occurrences(text, "COVID 19")) == ("COVID-19",)
    assert _hits(text, pp.find_occurrences(text, "2024")) == ("2024",)


def test_phrase_at_each_sentence_edge_is_found() -> None:
    text = "not happy here but eventually not happy"

    assert _hits(text, pp.find_occurrences(text, "not happy")) == (
        "not happy",
        "not happy",
    )


def test_overlapping_occurrences_count_every_start_position() -> None:
    text = "the the the"

    assert pp.find_occurrences(text, "the the") == ((0, 7), (4, 11))


def _mixed_ascii_case(value: str, rng: random.Random) -> str:
    return "".join(char.upper() if rng.getrandbits(1) else char.lower() for char in value)


def test_reported_offsets_are_exact_under_case_and_confusable_folding() -> None:
    """Fixed-seed property check: every reported slice is the normalized query."""
    rng = random.Random(44)
    vocabulary = ("algorithm", "design", "hick", "law", "covid", "model", "context")
    variants = {
        " ": (" ",),
        "-": ("-", "–", "—", "‐"),
        "'": ("'", "’", "‘", "ʼ"),
    }

    for _ in range(250):
        words = rng.sample(vocabulary, rng.randint(1, 4))
        separator = rng.choice(tuple(variants))
        query = separator.join(words)
        text_separator = rng.choice(variants[separator])
        source_phrase = text_separator.join(_mixed_ascii_case(word, rng) for word in words)
        text = f"prefix! {source_phrase} suffix."

        spans = pp.find_occurrences(text, query)

        assert len(spans) == 1
        hit = _hits(text, spans)[0]
        assert search_terms.fold(hit).casefold() == search_terms.fold(query).casefold()
        assert pp.phrase_words(hit) == pp.phrase_words(query)


def test_length_changing_casefold_falls_back_to_exact_case() -> None:
    text = "İ i"

    assert pp.phrase_words("İ") == ("İ",)
    assert pp.find_occurrences(text, "İ") == ((0, 1),)
    assert pp.find_occurrences(text, "i") == ((2, 3),)


def test_profile_reuses_the_search_term_fold_authority() -> None:
    assert pp.CONFUSABLE_CLASSES is search_terms.CONFUSABLE_CLASSES
    assert pp.fold is search_terms.fold

    expected_joiners = frozenset(
        member
        for confusable_class in search_terms.CONFUSABLE_CLASSES
        if confusable_class[0] in {"'", "-"}
        for member in confusable_class
    )
    assert pp.SURFACE_WORD_V1.joiners == expected_joiners
    assert not pp.SURFACE_WORD_V1.joiners.intersection(search_terms.CONFUSABLE_CLASSES[2])


def test_surface_word_v1_policy_is_named_immutable_and_explicit() -> None:
    assert pp.PROFILE_SURFACE_WORD_V1 == "surface_word_v1"
    assert pp.SURFACE_WORD_V1.identifier == pp.PROFILE_SURFACE_WORD_V1
    assert pp.SURFACE_WORD_V1.include_digits is True
    assert pp.SURFACE_WORD_V1.include_underscore is True
    assert pp.SURFACE_WORD_V1.count_overlaps is True

    with pytest.raises(FrozenInstanceError):
        pp.SURFACE_WORD_V1.include_digits = False  # type: ignore[misc]


def test_degenerate_inputs_are_safe_and_single_words_remain_valid() -> None:
    assert pp.word_spans("") == ()
    assert pp.phrase_words("") == ()
    assert pp.phrase_words("... — !!!") == ()
    assert pp.find_occurrences("text", "") == ()
    assert pp.find_occurrences("text", "... — !!!") == ()
    assert pp.find_occurrences("short", "a phrase longer than the text") == ()
    assert pp.find_occurrences("A single word.", "single") == ((2, 8),)


def test_nlp_architecture_names_the_versioned_phrase_profile() -> None:
    architecture = (REPO_ROOT / "docs" / "nlp_architecture.md").read_text(encoding="utf-8")
    retrieval_section = architecture.split("## 7. Search and retrieval paths", maxsplit=1)[1]

    assert "`surface_word_v1`" in retrieval_section
    assert "`domain/phrase_profile.py`" in retrieval_section
