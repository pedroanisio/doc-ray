"""Tests for the pure lexeme logic (identity, tokenizer, analyzer, detection)."""

from __future__ import annotations

import re

import pytest

from doc_ray.domain import lexemes as lex

# --- identity ---------------------------------------------------------------


def test_canonical_key_normalizes():
    assert lex.canonical_key("Running", "verb", "EN") == "en:VERB:running"
    # NFC + casefold: accented + case folded
    assert lex.canonical_key("Café", "NOUN", "fr") == "fr:NOUN:café"


def test_canonical_key_is_stable():
    a = lex.canonical_key("run", "VERB", "en")
    b = lex.canonical_key(" run ", "verb", "EN")
    assert a == b == "en:VERB:run"


def test_uid_candidate_format_and_growth():
    key = lex.canonical_key("run", "VERB", "en")
    u8 = lex.uid_candidate(key, "VERB", "en", 8)
    assert re.fullmatch(r"lx_en\.v_[0-9a-f]{8}", u8)
    u10 = lex.uid_candidate(key, "VERB", "en", 10)
    assert u10.startswith("lx_en.v_")  # same namespaced prefix
    assert u10[: len("lx_en.v_") + 8] == u8  # grown alias extends the shorter
    assert len(u10.split("_")[-1]) == 10 and u10 != u8


def test_pos_tag_defaults_to_x():
    assert lex.pos_tag("VERB") == "v"
    assert lex.pos_tag("NOUN") == "n"
    assert lex.pos_tag("WHATEVER") == "x"


# --- tokenizer offsets ------------------------------------------------------


def test_iter_words_offsets_recover_surface():
    text = "The cats ran, and dogs don't well-being."
    for surface, start, end in lex.iter_words(text):
        assert text[start:end] == surface


def test_iter_words_keeps_internal_apostrophe_and_hyphen():
    words = [w for w, _, _ in lex.iter_words("don't well-being e-mail")]
    assert words == ["don't", "well-being", "e-mail"]


def test_iter_words_excludes_digits_and_punct():
    words = [w for w, _, _ in lex.iter_words("abc 123 4.5 x9y !!")]
    # digits are not words; "x9y" splits into x, y (digit breaks the run)
    assert words == ["abc", "x", "y"]


# --- rule analyzer ----------------------------------------------------------


@pytest.mark.parametrize(
    "surface,lemma",
    [
        ("runs", "run"),
        ("cats", "cat"),
        ("cities", "city"),
        ("buses", "bus"),
        ("boxes", "box"),
        ("kiss", "kiss"),  # -ss not stripped
        ("run", "run"),
        ("is", "is"),  # too short to strip
    ],
)
def test_rule_analyzer_deinflects(surface, lemma):
    got_lemma, pos = lex.RuleAnalyzer().lemma(surface)
    assert got_lemma == lemma
    assert pos == "X"  # rule engine cannot tag POS


# --- language detection -----------------------------------------------------


def test_detect_english():
    text = "The report is about the accident and the crew of the aircraft."
    assert lex.detect_language(text) == "en"


def test_detect_portuguese():
    text = "A metodologia que verifica antes de afirmar e a estrutura do processo."
    assert lex.detect_language(text) == "pt"


def test_detect_weak_falls_back_to_default():
    assert lex.detect_language("xyz qwm zzt", default="en") == "en"
    assert lex.detect_language("", default="pt") == "pt"
