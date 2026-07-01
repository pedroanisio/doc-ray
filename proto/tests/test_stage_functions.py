"""Unit tests for the individual pipeline-stage functions of proto/headings.py.

Targets: extract_lines, body_size, _leadings, score_line.

These test the stages in isolation (constructing Line objects directly or
building a tiny synthetic PDF) rather than the end-to-end extract_headings.
Every assertion pins a concrete value/behavior so that a regression in the
function under test would flip a test red.
"""

from __future__ import annotations

import headings
from headings import Line
from synthpdf import body_block, heading


# --- helpers ---------------------------------------------------------------

def _mkline(text, size=10.0, bold=False, page=0, y0=0.0, y1=10.0,
            x0=72.0, x1=200.0, font="TestFont"):
    return Line(text=text, size=size, bold=bold, font=font, page=page,
                y0=y0, y1=y1, x0=x0, x1=x1)


# --- extract_lines ---------------------------------------------------------

def test_extract_lines_typography_roundtrip(make_pdf, open_doc):
    """extract_lines must recover per-line size/weight and stripped text:
    a bold big title above plain body prose."""
    path = make_pdf([heading("Big Bold Title"), *body_block(5)])
    doc = open_doc(path)
    try:
        lines = headings.extract_lines(doc)
    finally:
        doc.close()

    # 1 title + 5 body lines, all extracted.
    assert len(lines) == 6

    title = lines[0]
    body = lines[1:]

    # Text round-trips exactly (stripped, no leading/trailing whitespace).
    assert title.text == "Big Bold Title"
    assert title.text == title.text.strip()

    # The title is bold; the body prose is not.
    assert title.bold is True
    assert all(b.bold is False for b in body)

    # The title's font size is strictly larger than every body line's.
    assert all(title.size > b.size for b in body)
    # And the body lines share the modal small size.
    assert all(b.size == 10.0 for b in body)
    assert title.size == 16.0

    # Body text also round-trips stripped (distinct prose per line -> no dupes).
    for b in body:
        assert b.text == b.text.strip()
        assert b.text  # non-empty
    assert len({b.text for b in body}) == len(body)


def test_extract_lines_all_lines_carry_page_and_geometry(make_pdf, open_doc):
    """Every extracted line records its (0-based) page and a positive-height
    bounding box; a single-page doc yields only page 0."""
    path = make_pdf([heading("Solo Heading"), *body_block(3)])
    doc = open_doc(path)
    try:
        lines = headings.extract_lines(doc)
    finally:
        doc.close()

    assert lines, "expected some lines"
    for ln in lines:
        assert ln.page == 0
        assert ln.y1 > ln.y0          # non-degenerate vertical extent
        assert ln.x1 >= ln.x0
        assert isinstance(ln.size, float)


# --- body_size -------------------------------------------------------------

def test_body_size_is_char_weighted_modal_size():
    """body_size weights each size by character count, so a couple of long
    size-10 lines beat one short size-16 line -> returns 10.0.

    Negative control: if body_size used a *line count* (unweighted) mode with
    many short big lines, this would return 16.0 instead.
    """
    long_text = "x" * 60          # 60 chars each at body size
    lines = [
        _mkline(long_text, size=10.0),
        _mkline(long_text, size=10.0),
        _mkline("YY", size=16.0),  # 2 chars at a big size
        _mkline("Z", size=16.0),   # 1 char at a big size
    ]
    # size 10 accumulates 120 chars; size 16 accumulates only 3.
    assert headings.body_size(lines) == 10.0


def test_body_size_one_big_short_line_cannot_outweigh_body():
    """A single short size-16 line must not flip the modal body size even
    though it is the *first* line: char weight, not order/count, decides."""
    lines = [
        _mkline("A", size=16.0),                 # 1 char, big
        _mkline("body text line one here", size=10.0),   # 23 chars
        _mkline("body text line two here", size=10.0),   # 23 chars
    ]
    assert headings.body_size(lines) == 10.0

    # And when the big size genuinely dominates by char weight, it wins:
    big_wins = [
        _mkline("a" * 100, size=13.0),
        _mkline("x", size=10.0),
    ]
    assert headings.body_size(big_wins) == 13.0


def test_body_size_empty_defaults_to_12():
    """No lines -> documented fallback of 12.0 (not a crash / not 0)."""
    assert headings.body_size([]) == 12.0


# --- _leadings -------------------------------------------------------------

def test_leadings_positive_gaps_same_page_only():
    """_leadings returns the positive inter-line vertical gaps within a page;
    a page boundary contributes NO gap (prev/cur on different pages)."""
    lines = [
        _mkline("a", page=0, y0=100.0, y1=110.0),
        _mkline("b", page=0, y0=120.0, y1=130.0),   # gap 120-110 = 10
        _mkline("c", page=0, y0=145.0, y1=155.0),   # gap 145-130 = 15
        _mkline("d", page=1, y0=50.0, y1=60.0),     # new page -> skipped
        _mkline("e", page=1, y0=80.0, y1=90.0),     # gap 80-60 = 20
    ]
    assert headings._leadings(lines) == [10.0, 15.0, 20.0]


def test_leadings_ignores_overlapping_or_backwards_lines():
    """Only gaps where the current line starts at/below the previous line's
    bottom count; an overlapping (y0 < prev.y1) line contributes nothing."""
    lines = [
        _mkline("a", page=0, y0=100.0, y1=120.0),
        _mkline("b", page=0, y0=110.0, y1=125.0),   # y0 < prev.y1 -> skipped
        _mkline("c", page=0, y0=140.0, y1=150.0),   # gap 140-125 = 15
    ]
    assert headings._leadings(lines) == [15.0]


def test_leadings_single_line_and_empty():
    """A lone line has no predecessor -> no gaps; empty input -> empty list."""
    assert headings._leadings([_mkline("only", page=0)]) == []
    assert headings._leadings([]) == []


# --- score_line ------------------------------------------------------------

_EXPECTED_SIGNAL_KEYS = {"size_ratio", "bold", "short", "gap", "num", "allcaps"}


def test_score_line_big_bold_short_numbered_scores_high():
    """A big (>=1.15x body), bold, short, gap-preceded, numbered line is a
    strong heading candidate: score >= 0.5 with corroborating signals."""
    col_width = 450.0
    ln = _mkline("1 Introduction", size=16.0, bold=True,
                 x0=72.0, x1=200.0)   # narrow -> short
    score, sig = headings.score_line(
        ln, s_body=10.0, median_leading=13.0, col_width=col_width, prev_gap=30.0
    )

    assert sig.keys() == _EXPECTED_SIGNAL_KEYS
    assert score >= 0.5
    assert sig["size_ratio"] >= 1.15          # 16/10 = 1.6
    assert sig["bold"] is True
    assert sig["num"] is True
    assert sig["short"] is True
    assert sig["gap"] is True                  # 30 > 1.5 * 13
    assert sig["allcaps"] is False


def test_score_line_plain_wide_body_sentence_scores_low():
    """A plain, full-width, non-bold, non-numbered line ending in '.' is body,
    not a heading: low score and the discriminating signals are all False."""
    col_width = 450.0
    body = ("The lorem ipsum dolor sit amet consectetur adipiscing elit sed do "
            "eiusmod tempor incididunt labore magna aliqua enim minim veniam.")
    ln = _mkline(body, size=10.0, bold=False,
                 x0=72.0, x1=72.0 + col_width)  # fills the column -> not short
    score, sig = headings.score_line(
        ln, s_body=10.0, median_leading=13.0, col_width=col_width, prev_gap=13.0
    )

    assert sig.keys() == _EXPECTED_SIGNAL_KEYS
    assert score < 0.5
    assert sig["bold"] is False
    assert sig["num"] is False
    assert sig["short"] is False
    assert sig["size_ratio"] < 1.15            # same size as body
    # Sentence-terminated body: score is heavily penalized (halved).
    assert score == 0.0


def test_score_line_sentence_end_halves_score():
    """The RE_SENTENCE_END penalty is real: an otherwise identical big-bold
    line scores strictly lower when it ends like a sentence."""
    col_width = 450.0
    common = dict(size=16.0, bold=True, x0=72.0, x1=200.0)
    kw = dict(s_body=10.0, median_leading=13.0, col_width=col_width, prev_gap=5.0)

    clean, _ = headings.score_line(_mkline("Results and Discussion", **common), **kw)
    ended, _ = headings.score_line(_mkline("Results and Discussion.", **common), **kw)

    assert ended == clean * 0.5
    assert ended < clean


def test_score_line_allcaps_short_signal():
    """An all-caps short line (no lowercase, few words) sets allcaps=True; a
    long all-caps run (>12 words) does not qualify."""
    col_width = 450.0
    kw = dict(s_body=10.0, median_leading=13.0, col_width=col_width, prev_gap=5.0)

    short_caps = _mkline("INTRODUCTION AND SCOPE", size=10.0, bold=False,
                         x0=72.0, x1=180.0)
    _, sig_short = headings.score_line(short_caps, **kw)
    assert sig_short["allcaps"] is True

    long_caps_text = " ".join(["WORD"] * 13)   # 13 words > 12 -> disqualified
    long_caps = _mkline(long_caps_text, size=10.0, bold=False,
                        x0=72.0, x1=180.0)
    _, sig_long = headings.score_line(long_caps, **kw)
    assert sig_long["allcaps"] is False
