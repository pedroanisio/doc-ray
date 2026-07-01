"""Tests for the core logic: reflow + sentence splitting."""

from __future__ import annotations

import pytest

import extract_phrases as ep


# ---------------------------------------------------------------------------
# reflow — rejoin wrapped lines
# ---------------------------------------------------------------------------

def test_reflow_joins_wrapped_lines_with_a_space():
    assert ep.reflow("The cat\nsat down.") == "The cat sat down."


def test_reflow_dehyphenates_words_split_across_a_line_break():
    assert ep.reflow("low-\naltitude flight") == "lowaltitude flight"


def test_reflow_preserves_paragraph_breaks():
    # A blank line separates paragraphs and survives as a single newline.
    assert ep.reflow("First para.\n\nSecond para.") == "First para.\nSecond para."


def test_reflow_collapses_runs_of_whitespace_and_nbsp():
    assert ep.reflow("a\xa0\xa0b") == "a b"


def test_reflow_normalises_curly_apostrophe():
    assert ep.reflow("it’s here") == "it's here"


def test_reflow_ignores_blank_paragraphs():
    assert ep.reflow("\n\n  \n\nOnly this.\n\n") == "Only this."


# ---------------------------------------------------------------------------
# split_sentences — boundary detection
# ---------------------------------------------------------------------------

def test_splits_on_period_exclamation_and_question():
    assert ep.split_sentences("One is here. Two is here! Three is here?") == [
        "One is here.",
        "Two is here!",
        "Three is here?",
    ]


def test_does_not_split_on_semicolon_or_colon():
    # A sentence is bounded by . ! ? only — not ; or :.
    assert ep.split_sentences("First part; second part: third part done.") == [
        "First part; second part: third part done."
    ]


@pytest.mark.parametrize("abbr_sentence", [
    "Dr. Silva arrived early. He waited.",
    "See Fig. 3 for details. It helps.",
    "The item costs 5 etc. and more. Done here.",
])
def test_abbreviations_do_not_cause_a_split(abbr_sentence):
    parts = ep.split_sentences(abbr_sentence)
    # The abbreviation stays inside its sentence, so there are exactly 2 parts.
    assert len(parts) == 2
    assert parts[0].count(".") >= 1  # abbreviation dot retained


def test_single_letter_initials_do_not_split():
    assert ep.split_sentences("J. R. Smith wrote it.") == ["J. R. Smith wrote it."]


def test_decimals_do_not_split():
    assert ep.split_sentences("It cost 3.5 million dollars. That is a lot.") == [
        "It cost 3.5 million dollars.",
        "That is a lot.",
    ]


def test_split_on_boundary_before_a_digit_or_quote():
    # A new sentence may start with a digit or an opening quote.
    assert ep.split_sentences('He spoke. "Later," she said.') == [
        "He spoke.",
        '"Later," she said.',
    ]


def test_lowercase_after_period_is_not_a_boundary():
    # ".com" style — the period is not followed by a sentence start.
    assert ep.split_sentences("Visit example.com today. Thanks.") == [
        "Visit example.com today.",
        "Thanks.",
    ]


def test_collapses_internal_whitespace_in_each_sentence():
    assert ep.split_sentences("Too    many    spaces here.") == ["Too many spaces here."]


# ---------------------------------------------------------------------------
# split_sentences — min_words filter
# ---------------------------------------------------------------------------

def test_min_words_drops_short_fragments():
    text = "Hi. This sentence is clearly long enough."
    assert ep.split_sentences(text, min_words=3) == ["This sentence is clearly long enough."]


def test_min_words_one_keeps_everything():
    assert ep.split_sentences("Yes. No. Maybe.", min_words=1) == ["Yes.", "No.", "Maybe."]


def test_empty_and_whitespace_input_returns_empty_list():
    assert ep.split_sentences("") == []
    assert ep.split_sentences("   \n  ") == []
    assert ep.extract_sentences("") == []


# ---------------------------------------------------------------------------
# extract_sentences — reflow + split end to end
# ---------------------------------------------------------------------------

def test_extract_sentences_reflows_then_splits():
    # A single sentence wrapped across three physical lines becomes one item.
    raw = "The aircraft climbed\nto a high altitude and\nthen levelled off. It cruised."
    assert ep.extract_sentences(raw) == [
        "The aircraft climbed to a high altitude and then levelled off.",
        "It cruised.",
    ]


def test_extract_sentences_returns_list_of_nonempty_strings():
    out = ep.extract_sentences("A first sentence. A second one here.")
    assert isinstance(out, list)
    assert all(s and s.strip() == s for s in out)


# ---------------------------------------------------------------------------
# extract_paragraphs — group sentences by paragraph
# ---------------------------------------------------------------------------

def test_paragraphs_group_sentences_by_blank_line():
    raw = (
        "First paragraph sentence one. First paragraph sentence two.\n\n"
        "Second paragraph only sentence."
    )
    assert ep.extract_paragraphs(raw) == [
        ["First paragraph sentence one.", "First paragraph sentence two."],
        ["Second paragraph only sentence."],
    ]


def test_paragraphs_reflow_wrapped_lines_within_a_paragraph():
    # Wrapped lines inside one paragraph stay in the same group.
    raw = "The cat sat\non the mat. It slept.\n\nA new day began."
    assert ep.extract_paragraphs(raw) == [
        ["The cat sat on the mat.", "It slept."],
        ["A new day began."],
    ]


def test_paragraphs_flatten_equals_extract_sentences():
    # Flattening the groups must equal the flat extractor's output.
    raw = "One two three. Four five six.\n\nSeven eight nine here."
    flat = ep.extract_sentences(raw)
    grouped = ep.extract_paragraphs(raw)
    assert [s for group in grouped for s in group] == flat


def test_paragraphs_drops_empty_groups_and_applies_min_words():
    raw = "Hi.\n\nThis paragraph is clearly long enough to keep."
    # "Hi." (1 word) is filtered, leaving its paragraph empty and dropped.
    assert ep.extract_paragraphs(raw, min_words=3) == [
        ["This paragraph is clearly long enough to keep."]
    ]


def test_paragraphs_empty_input_returns_empty_list():
    assert ep.extract_paragraphs("") == []
