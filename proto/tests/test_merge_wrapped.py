"""Unit tests for headings._merge_wrapped.

_merge_wrapped((Line, score, signals) tuples, median_leading) collapses a
heading that visually wrapped onto a following line into a single candidate.

A MERGE (2 -> 1) requires ALL of:
  - same page
  - same round(size) AND same bold
  - tight vertical gap:  0 <= y0_next - y1_prev <= 1.4 * median_leading
  - first line does NOT end in a sentence char ([.?!;:,])
  - second line does NOT match RE_DOTNUM (i.e. is not itself numbered)

On merge the surviving candidate's text is 'first second', its y1 is extended
to the second line's y1, and its score is max(first, second). Signals are
kept from the FIRST line.

All Line objects are built directly with explicit geometry. median_leading is
fixed at 12.0, so the tight-gap ceiling is 1.4 * 12.0 = 16.8 points.
"""

from __future__ import annotations

import headings
from headings import Line

ML = 12.0  # median_leading for every test; tight ceiling = 1.4 * ML = 16.8


def _mk(text, size, bold, page, y0, y1):
    """Build a Line with fixed x geometry (x irrelevant to _merge_wrapped)."""
    return Line(text=text, size=size, bold=bold, font="hebo" if bold else "helv",
                page=page, y0=y0, y1=y1, x0=72.0, x1=300.0)


def _merge(a, score_a, b, score_b, sig_a=None, sig_b=None):
    sig_a = sig_a if sig_a is not None else {"who": "a"}
    sig_b = sig_b if sig_b is not None else {"who": "b"}
    return headings._merge_wrapped([(a, score_a, sig_a), (b, score_b, sig_b)], ML)


# --- The positive (merge happens) case ------------------------------------

def test_merges_wrapped_heading_into_one_candidate():
    # y1_prev=112, y0_next=118  -> gap = 6.0, comfortably <= 16.8
    a = _mk("A Wrapped Heading That", 16.0, True, page=0, y0=100.0, y1=112.0)
    b = _mk("continues on line two", 16.0, True, page=0, y0=118.0, y1=130.0)
    out = _merge(a, 0.6, b, 0.9, sig_a={"src": "first"}, sig_b={"src": "second"})

    assert len(out) == 1, "same-style tight continuation must collapse to 1"
    mline, mscore, msig = out[0]
    # merged text is 'first second'
    assert mline.text == "A Wrapped Heading That continues on line two"
    # y1 extended to the SECOND line's y1
    assert mline.y1 == 130.0
    # y0 stays the first line's y0
    assert mline.y0 == 100.0
    # score is the max of the two
    assert mscore == 0.9
    # signals are carried from the FIRST line
    assert msig == {"src": "first"}


def test_merge_score_is_max_when_first_is_larger():
    # Guard against a min/second-wins regression: first score must win here.
    a = _mk("Heading fragment one", 16.0, True, page=0, y0=100.0, y1=112.0)
    b = _mk("fragment two", 16.0, True, page=0, y0=116.0, y1=128.0)
    out = _merge(a, 0.95, b, 0.30)
    assert len(out) == 1
    assert out[0][1] == 0.95


def test_merges_when_sizes_differ_but_round_equal():
    # 15.6 and 16.4 both round() to 16 -> same style; must still merge.
    a = _mk("Rounds to sixteen here", 15.6, True, page=0, y0=100.0, y1=112.0)
    b = _mk("and here as well", 16.4, True, page=0, y0=117.0, y1=129.0)
    out = _merge(a, 0.6, b, 0.6)
    assert len(out) == 1
    assert out[0][0].text == "Rounds to sixteen here and here as well"


def test_three_line_wrap_all_collapse():
    # A wrap can chain: each new line merges into the running-merged candidate.
    a = _mk("Part one of a heading", 16.0, True, page=0, y0=100.0, y1=112.0)
    b = _mk("part two of a heading", 16.0, True, page=0, y0=116.0, y1=128.0)
    c = _mk("part three of it", 16.0, True, page=0, y0=132.0, y1=144.0)
    out = headings._merge_wrapped(
        [(a, 0.5, {"k": 1}), (b, 0.6, {"k": 2}), (c, 0.7, {"k": 3})], ML)
    assert len(out) == 1
    mline, mscore, msig = out[0]
    assert mline.text == "Part one of a heading part two of a heading part three of it"
    assert mline.y1 == 144.0
    assert mscore == 0.7           # max across all three
    assert msig == {"k": 1}        # first line's signals


# --- Negative (do NOT merge) cases ----------------------------------------

def test_no_merge_when_gap_too_large():
    # gap = 20.0 > 16.8 ceiling -> stays two separate candidates.
    a = _mk("First heading line", 16.0, True, page=0, y0=100.0, y1=112.0)
    b = _mk("second heading line", 16.0, True, page=0, y0=132.0, y1=144.0)
    out = _merge(a, 0.6, b, 0.9)
    assert len(out) == 2
    assert [o[0].text for o in out] == ["First heading line", "second heading line"]
    # untouched geometry
    assert out[0][0].y1 == 112.0
    assert out[0][1] == 0.6


def test_no_merge_on_negative_gap():
    # Overlapping / out-of-order lines (y0_next < y1_prev) fail the 0 <= gap test.
    a = _mk("First heading line", 16.0, True, page=0, y0=100.0, y1=112.0)
    b = _mk("second heading line", 16.0, True, page=0, y0=108.0, y1=120.0)
    out = _merge(a, 0.6, b, 0.9)
    assert len(out) == 2


def test_no_merge_when_size_differs():
    # round(16) != round(20) -> different style, no merge.
    a = _mk("Big heading line", 16.0, True, page=0, y0=100.0, y1=112.0)
    b = _mk("smaller heading line", 20.0, True, page=0, y0=116.0, y1=128.0)
    out = _merge(a, 0.6, b, 0.9)
    assert len(out) == 2


def test_no_merge_when_bold_differs():
    # same size, but bold flag differs -> not same style.
    a = _mk("Bold heading line", 16.0, True, page=0, y0=100.0, y1=112.0)
    b = _mk("regular heading line", 16.0, False, page=0, y0=116.0, y1=128.0)
    out = _merge(a, 0.6, b, 0.9)
    assert len(out) == 2


def test_no_merge_across_pages():
    # tight gap by coordinates, same style, but different page -> no merge.
    a = _mk("Bottom of page one", 16.0, True, page=0, y0=100.0, y1=112.0)
    b = _mk("top of page two", 16.0, True, page=1, y0=116.0, y1=128.0)
    out = _merge(a, 0.6, b, 0.9)
    assert len(out) == 2
    assert out[1][0].page == 1


def test_no_merge_when_first_ends_with_sentence_char():
    # First line ends in '.', so it is sentence-terminated -> not a continuation.
    a = _mk("A complete sentence here.", 16.0, True, page=0, y0=100.0, y1=112.0)
    b = _mk("another fragment", 16.0, True, page=0, y0=116.0, y1=128.0)
    out = _merge(a, 0.6, b, 0.9)
    assert len(out) == 2
    assert out[0][0].text == "A complete sentence here."


def test_no_merge_when_first_ends_with_colon():
    # RE_SENTENCE_END also covers ':' -> still treated as terminated.
    a = _mk("Consider the following:", 16.0, True, page=0, y0=100.0, y1=112.0)
    b = _mk("an enumerated item", 16.0, True, page=0, y0=116.0, y1=128.0)
    out = _merge(a, 0.6, b, 0.9)
    assert len(out) == 2


def test_no_merge_when_second_is_numbered():
    # Second line matches RE_DOTNUM ('2 Next Section') -> it is its own heading.
    a = _mk("An Unterminated Heading", 16.0, True, page=0, y0=100.0, y1=112.0)
    b = _mk("2 Next Section", 16.0, True, page=0, y0=116.0, y1=128.0)
    out = _merge(a, 0.6, b, 0.9)
    assert len(out) == 2
    assert out[1][0].text == "2 Next Section"


def test_no_merge_when_second_is_dotted_number():
    # RE_DOTNUM also matches dotted depth like '1.2.3 Something'.
    a = _mk("An Unterminated Heading", 16.0, True, page=0, y0=100.0, y1=112.0)
    b = _mk("1.2.3 Subsection", 16.0, True, page=0, y0=116.0, y1=128.0)
    out = _merge(a, 0.6, b, 0.9)
    assert len(out) == 2


# --- Structural / edge behaviors ------------------------------------------

def test_single_candidate_passes_through_unchanged():
    a = _mk("Lonely heading", 16.0, True, page=0, y0=100.0, y1=112.0)
    out = headings._merge_wrapped([(a, 0.7, {"x": 1})], ML)
    assert len(out) == 1
    assert out[0] == (a, 0.7, {"x": 1})
    assert out[0][0].text == "Lonely heading"


def test_empty_candidates_returns_empty():
    assert headings._merge_wrapped([], ML) == []


def test_only_adjacent_pair_merges_not_the_isolated_third():
    # a,b merge (tight); c is far below -> stays separate. Result: 2 items.
    a = _mk("Wrapped part one", 16.0, True, page=0, y0=100.0, y1=112.0)
    b = _mk("wrapped part two", 16.0, True, page=0, y0=116.0, y1=128.0)
    c = _mk("Unrelated Later Heading", 16.0, True, page=0, y0=300.0, y1=312.0)
    out = headings._merge_wrapped(
        [(a, 0.6, {}), (b, 0.9, {}), (c, 0.5, {})], ML)
    assert len(out) == 2
    assert out[0][0].text == "Wrapped part one wrapped part two"
    assert out[0][1] == 0.9
    assert out[1][0].text == "Unrelated Later Heading"
    assert out[1][1] == 0.5
