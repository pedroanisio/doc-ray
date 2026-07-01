"""Tests for the embedded-TOC fast path of headings.extract_headings.

The fast path in extract_headings triggers when a document carries an embedded
outline (doc.get_toc) that spans MORE THAN ONE distinct page. When it fires it
trusts the author's outline verbatim: text/level/page come straight from the
TOC, confidence is pinned to 1.0, and every heading is tagged
signals={'source': 'toc'}. Levels are capped at 4 (min(lvl, 4)).

These tests cover:
  * the positive canonical case (toc_pdf) — exact texts/levels/pages/signals,
  * the guard that the fast path requires >1 distinct page (a single-page TOC
    must fall through to the heuristic and NOT echo the outline verbatim),
  * the level cap (a deep TOC level collapses to 4).
"""

from __future__ import annotations

import headings
from invariants import assert_valid_outline, levels, texts
from synthpdf import L, body_block


# --- Positive: canonical embedded-TOC document ----------------------------

def test_toc_fast_path_exact_outline(toc_pdf, open_doc):
    """toc_pdf carries a 3-entry outline across pages 2/3/4 -> verbatim outline."""
    doc = open_doc(toc_pdf)
    hs = headings.extract_headings(doc)

    # Exact ground truth of the embedded outline.
    assert texts(hs) == ["Chapter One", "Section 1.1", "Chapter Two"]
    assert levels(hs) == [1, 2, 1]
    # get_toc pages are 1-based; the fast path stores 0-based page.
    assert [h.page for h in hs] == [1, 2, 3]

    # The TOC path pins confidence and tags the source.
    assert all(h.confidence == 1.0 for h in hs)
    assert all(h.signals == {"source": "toc"} for h in hs)

    # Ordinals are the enumerate() index: strictly increasing from 0.
    assert [h.ordinal for h in hs] == [0, 1, 2]

    # Structural invariants (TOC path trusts author nesting -> no monotonic req).
    assert_valid_outline(hs, page_count=doc.page_count, require_monotonic=False)


def test_toc_fast_path_wins_over_rendered_typography(toc_pdf, open_doc):
    """Even though 'Chapter One' is rendered at 18pt and 'Section 1.1' at 14pt,
    the outline's authored levels (1, 2, 1) are used verbatim rather than a
    typography-clustered ranking (which would not put the two 18pt chapters at
    the same level as an outline built from font size alone might scramble)."""
    doc = open_doc(toc_pdf)
    hs = headings.extract_headings(doc)
    # 'Chapter One' and 'Chapter Two' are both authored level 1 despite being
    # rendered at the SAME size as each other but the middle one is a subsection.
    by_text = {h.text: h.level for h in hs}
    assert by_text["Chapter One"] == 1
    assert by_text["Chapter Two"] == 1
    assert by_text["Section 1.1"] == 2


# --- Negative: fast path needs the TOC to span >1 distinct page -----------

def test_single_page_toc_does_not_trigger_fast_path(make_pdf, open_doc):
    """An outline whose entries all point at page 1 spans only ONE distinct
    page, so the >1-distinct-page guard blocks the fast path. The two body-less
    'A'/'B' entries are never rendered on the page, so they must NOT reappear as
    verbatim page-1 headings via the heuristic path either."""
    # Real prose only; the 'A'/'B' outline titles are not drawn anywhere.
    path = make_pdf(body_block(6), toc=[[1, "A", 1], [1, "B", 1]], name="onepgtoc.pdf")
    doc = open_doc(path)

    # Sanity: the outline really is present but collapses to a single page.
    toc = doc.get_toc(simple=True)
    assert {e[2] for e in toc} == {1}, "expected a single-distinct-page outline"

    hs = headings.extract_headings(doc)

    # The fast path did NOT fire: nothing is tagged as a TOC-sourced heading...
    assert all(h.signals.get("source") != "toc" for h in hs)
    assert all(h.confidence != 1.0 for h in hs)  # heuristic never pins 1.0
    # ...and the un-rendered outline titles are absent from the output.
    out = texts(hs)
    assert "A" not in out
    assert "B" not in out
    # Plain prose paragraphs are not headings either -> empty outline here.
    assert hs == []


def test_multi_distinct_page_toc_does_trigger_fast_path(make_pdf, open_doc):
    """Companion to the negative above: the SAME two entries, but now spanning
    two distinct pages, DO trigger the fast path and echo the outline verbatim
    (confirming the discriminator is the distinct-page count, not the text)."""
    lines = (
        body_block(3)
        + [L("A", 16, True, gap_before=18)] + body_block(3)
        + [L("B", 16, True, new_page=True, gap_before=18)] + body_block(3)
    )
    path = make_pdf(lines, toc=[[1, "A", 1], [1, "B", 2]], name="twopgtoc.pdf")
    doc = open_doc(path)
    assert {e[2] for e in doc.get_toc(simple=True)} == {1, 2}

    hs = headings.extract_headings(doc)
    assert texts(hs) == ["A", "B"]
    assert all(h.signals == {"source": "toc"} for h in hs)
    assert all(h.confidence == 1.0 for h in hs)
    # Pages are 0-based derived from the 1-based outline pages.
    assert [h.page for h in hs] == [0, 1]


# --- Level cap: min(lvl, 4) -----------------------------------------------

def test_toc_level_capped_at_four(make_pdf, open_doc):
    """A TOC level deeper than 4 is clamped to 4. PyMuPDF only accepts a
    monotonically climbing hierarchy, so we ramp 1..6 across two pages; the
    level-6 'Deep' entry must land at level 4, and level-5 'E' also caps at 4."""
    lines = body_block(2) + [L("Deep", 18, True, new_page=True)] + body_block(2)
    toc = [
        [1, "A", 1], [2, "B", 1], [3, "C", 1],
        [4, "D", 1], [5, "E", 1], [6, "Deep", 2],
    ]
    path = make_pdf(lines, toc=toc, name="levelcap.pdf")
    doc = open_doc(path)
    hs = headings.extract_headings(doc)

    by_text = {h.text: h.level for h in hs}
    assert by_text["A"] == 1
    assert by_text["D"] == 4
    assert by_text["E"] == 4      # min(5, 4)
    assert by_text["Deep"] == 4   # min(6, 4)

    # No level ever exceeds the cap, and the 'Deep' entry keeps its own page.
    assert max(levels(hs)) == 4
    assert by_text  # non-empty guard
    deep = next(h for h in hs if h.text == "Deep")
    assert deep.page == 1
    assert deep.confidence == 1.0
    assert deep.signals == {"source": "toc"}
