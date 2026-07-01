"""Unit tests for the pure regex + predicate guards in headings.py.

These exercise RE_DOTNUM, RE_KEYWORD, RE_ALLCAPS, RE_PAGENUM_PREFIX and the
_letters() helper in isolation, with explicit expected booleans / ints. They
are intentionally exhaustive and non-tautological: each parametrization pins a
concrete truth value so that a broken regex would flip at least one case.
"""

from __future__ import annotations

import pytest

import headings


# --------------------------------------------------------------------------
# RE_DOTNUM  — leading dotted-decimal numbering followed by a token.
# Pattern: ^(\d+(?:\.\d+){0,5})[.)]?\s+\S
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text, expected_group, expected_depth",
    [
        ("1 Intro", "1", 1),
        ("1.2 Background", "1.2", 2),
        ("1.2.3 Prior Work", "1.2.3", 3),
        ("10.4 Y", "10.4", 2),
    ],
)
def test_dotnum_matches_and_depth(text, expected_group, expected_depth):
    m = headings.RE_DOTNUM.match(text)
    assert m is not None, f"expected RE_DOTNUM to match {text!r}"
    # group(1) is the dotted-number token; the module derives dot-depth from it.
    assert m.group(1) == expected_group
    assert m.group(1).count(".") + 1 == expected_depth


@pytest.mark.parametrize(
    "text",
    [
        "Introduction",   # no leading digit at all
        "1",              # bare number, no trailing token / whitespace+\S
        "Word 1.2 here",  # number is not at the start (anchor ^ fails)
    ],
)
def test_dotnum_rejects(text):
    assert headings.RE_DOTNUM.match(text) is None, (
        f"expected RE_DOTNUM to REJECT {text!r}"
    )


@pytest.mark.parametrize(
    "text, expected",
    [
        # Optional trailing "." or ")" after the number is accepted.
        ("1. Intro", True),
        ("1) Intro", True),
        # Depth is capped at 6 components (\.\d+){0,5}; a 7-component chain
        # still matches greedily on the first 6 then needs whitespace+\S.
        ("1.2.3.4.5.6 x", True),
        ("1.2.3.4.5.6.7 x", False),
    ],
)
def test_dotnum_trailing_and_depth_bounds(text, expected):
    assert bool(headings.RE_DOTNUM.match(text)) is expected


# --------------------------------------------------------------------------
# RE_KEYWORD  — structural keyword prefixes (English + PT-BR, accented).
# Case-insensitive; requires a word boundary after the keyword.
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "Chapter 1",
        "Section 2",
        "Part I",
        "Appendix A",
        "Annex B",
        "Capitulo 1",     # unaccented PT-BR spelling
        "Capítulo 1",     # accented PT-BR spelling
        "Seção 3",
        "Parte 1",
        "Apêndice A",
        "CHAPTER 1",      # case-insensitive
        "section without number",  # keyword + \b still matches (only prefix tested)
    ],
)
def test_keyword_matches(text):
    assert headings.RE_KEYWORD.match(text) is not None, (
        f"expected RE_KEYWORD to match {text!r}"
    )


@pytest.mark.parametrize(
    "text",
    [
        "Chapters overview",     # 'chapter' + 's' -> no word boundary, no match
        "A section about x",     # keyword not at the start (^ anchor fails)
        "Sectionable",           # keyword glued to more letters -> no \b
        "Introduction",          # unrelated word
    ],
)
def test_keyword_rejects(text):
    assert headings.RE_KEYWORD.match(text) is None, (
        f"expected RE_KEYWORD to REJECT {text!r}"
    )


# --------------------------------------------------------------------------
# RE_ALLCAPS  — no lowercase letters, at least 4 chars.  ^[^a-z]{4,}$
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "PART ONE",       # spaces are not lowercase, 4+ chars
        "GUERRA É PAZ",   # accented uppercase É is not in [a-z]
        "ABCD",           # exactly 4 uppercase letters
    ],
)
def test_allcaps_matches(text):
    assert headings.RE_ALLCAPS.match(text) is not None, (
        f"expected RE_ALLCAPS to match {text!r}"
    )


@pytest.mark.parametrize(
    "text",
    [
        "Hello",       # contains lowercase
        "ab",          # lowercase and too short
        "Mixed Case",  # contains lowercase
        "ABC",         # only 3 chars -> below the 4-char floor
    ],
)
def test_allcaps_rejects(text):
    assert headings.RE_ALLCAPS.match(text) is None, (
        f"expected RE_ALLCAPS to REJECT {text!r}"
    )


# --------------------------------------------------------------------------
# RE_PAGENUM_PREFIX  — printed-TOC rows "NNN <dash> Title".
# ^\d{1,4}\s*[-–—]\s+\S   (hyphen, en dash, em dash)
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "006 - Capítulo 1",  # hyphen-minus
        "12 – Title",        # en dash (U+2013)
        "7 — X",             # em dash (U+2014)
    ],
)
def test_pagenum_prefix_matches(text):
    assert headings.RE_PAGENUM_PREFIX.match(text) is not None, (
        f"expected RE_PAGENUM_PREFIX to match {text!r}"
    )


@pytest.mark.parametrize(
    "text",
    [
        "1.2 Intro",       # dot, not a dash separator
        "Introduction",    # no leading digits
        "- bullet",        # dash but no leading digit
        "12345 - Title",   # 5 leading digits: \d{1,4} then '5' (not dash) -> fail
    ],
)
def test_pagenum_prefix_rejects(text):
    assert headings.RE_PAGENUM_PREFIX.match(text) is None, (
        f"expected RE_PAGENUM_PREFIX to REJECT {text!r}"
    )


# --------------------------------------------------------------------------
# _letters  — count of alphabetic characters, including accented letters.
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text, expected",
    [
        ("E", 1),          # single letter
        ("1 0", 0),        # digits and space only
        ("ção", 3),        # accented 'ç' + 'ã' + 'o' all count
        ("§ 4", 0),        # section sign + digit -> no alphabetic
        ("GUERRA É PAZ", 10),  # accented uppercase É counts; spaces do not
    ],
)
def test_letters_count(text, expected):
    assert headings._letters(text) == expected
