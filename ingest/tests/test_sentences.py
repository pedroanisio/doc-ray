"""Tests for sentence segmentation, offsets, and ULID assignment."""

from __future__ import annotations

import re

import pytest

from ingest.sentences import Sentence, build_sentences, split_sentences
from ingest.ulid import ULID_REGEX, ULIDFactory

_RE = re.compile(ULID_REGEX)


def _texts(text):
    return [s.text for s in split_sentences(text)]


# --- basic segmentation -----------------------------------------------------

def test_simple_three_sentences():
    assert _texts("Hello world. How are you? I am fine!") == [
        "Hello world.", "How are you?", "I am fine!",
    ]


def test_no_terminator_is_one_sentence():
    assert _texts("just a fragment with no end") == [
        "just a fragment with no end"
    ]


def test_empty_and_whitespace_yield_nothing():
    assert split_sentences("") == []
    assert split_sentences("   \n\t  ") == []


def test_internal_newlines_are_collapsed():
    # A hard line break inside one sentence (common in PDF text) is joined.
    assert _texts("Hello\nworld. Bye.") == ["Hello world.", "Bye."]


def test_ellipsis_and_multi_punct():
    assert _texts("Wait... What?! Really.") == ["Wait...", "What?!", "Really."]


def test_quoted_sentence_boundary():
    out = _texts('He said "Go now." Then she left.')
    assert out == ['He said "Go now."', "Then she left."]


# --- abbreviation / decimal / acronym guards -------------------------------

@pytest.mark.parametrize("text,expected_count", [
    ("Dr. Smith went home. He slept.", 2),        # title abbreviation
    ("The value is 3.14 today. Yes.", 2),          # decimal number
    ("The U.S. economy grew fast. Good.", 2),      # acronym U.S.
    ("See e.g. the manual. Done.", 2),             # e.g.
    ("Meet at 9 a.m. sharp. Bring ID.", 2),        # a.m.
    ("Item No. 5 is ready. Ship it.", 2),          # No.
    ("A. Lincoln spoke. People cheered.", 2),      # single initial
])
def test_false_boundaries_do_not_split(text, expected_count):
    assert len(split_sentences(text)) == expected_count


def test_real_period_still_splits():
    assert _texts("This ends here. This begins next.") == [
        "This ends here.", "This begins next.",
    ]


# --- offset contract --------------------------------------------------------

def test_offsets_locate_exact_span():
    text = "First one. Second two? Third three!"
    for s in split_sentences(text):
        # offsets recover the same tokens as the normalized text
        assert text[s.char_start:s.char_end].split() == s.text.split()
    sents = split_sentences(text)
    # offsets are ordered and non-overlapping
    assert sents[0].char_start < sents[1].char_start < sents[2].char_start
    for s in sents:
        assert s.char_end > s.char_start


def test_offsets_survive_leading_whitespace_and_newlines():
    text = "  Alpha beta.\n\n  Gamma delta."
    sents = split_sentences(text)
    assert [s.text for s in sents] == ["Alpha beta.", "Gamma delta."]
    for s in sents:
        assert text[s.char_start] != " "                       # trimmed
        assert text[s.char_start:s.char_end].split() == s.text.split()


# --- build_sentences: ULID + ordinal ---------------------------------------

def test_build_sentences_assigns_ulids_and_ordinals():
    rows = build_sentences("One. Two. Three.")
    assert [r["ordinal"] for r in rows] == [0, 1, 2]
    assert [r["text"] for r in rows] == ["One.", "Two.", "Three."]
    for r in rows:
        assert _RE.match(r["id"])                               # valid ULID
    ids = [r["id"] for r in rows]
    assert len(set(ids)) == 3                                   # unique
    assert ids == sorted(ids)                                   # monotonic


def test_build_sentences_ulids_track_reading_order():
    # Even within one call/ms, ULID order must equal ordinal order.
    rows = build_sentences(" ".join(f"Sentence {i}." for i in range(50)),
                           factory=ULIDFactory(clock=lambda: 1234, rng=lambda: 0))
    ids = [r["id"] for r in rows]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


def test_build_sentences_empty_text():
    assert build_sentences("") == []
    assert build_sentences(None) == []


def test_offsets_map_back_into_full_text():
    full = "Alpha is first. Beta comes second. Gamma is third."
    for r in build_sentences(full):
        assert full[r["char_start"]:r["char_end"]].split() == r["text"].split()
