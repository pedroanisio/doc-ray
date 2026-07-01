"""End-to-end heuristic extraction over the canonical fixtures.

Exercises ``headings.extract_headings`` on the heuristic (no embedded outline)
path for the canonical synthetic documents and asserts the VERIFIED
ground-truth: heading counts, text membership, the exact level sequence for
the numbered document, and the universal structural invariants. Each case also
pins the key NEGATIVE behaviors (junk / running-heads / printed-TOC rows /
body false-positives filtered out) so a regression that let noise leak in — or
that dropped a real heading — would fail loudly.
"""

from __future__ import annotations

import headings
from invariants import assert_valid_outline, levels, texts


# --- numbered_pdf ----------------------------------------------------------

def test_numbered_pdf_exact_texts_and_levels(numbered_pdf, open_doc):
    doc = open_doc(numbered_pdf)
    hs = headings.extract_headings(doc)

    assert len(hs) == 4
    assert texts(hs) == [
        "1 Introduction",
        "1.1 Background",
        "1.1.1 Prior Work",
        "2 Methods",
    ]
    # dot-depth drives the level: 1 / 1.1 / 1.1.1 / 2 -> 1,2,3,1
    assert levels(hs) == [1, 2, 3, 1]
    # every scored heading here carries confidence 0.9 (num_strong corroboration)
    assert all(h.confidence == 0.9 for h in hs)
    assert_valid_outline(hs, page_count=doc.page_count, require_monotonic=True)


# --- wrapped_title_pdf -----------------------------------------------------

_MERGED_TITLE = (
    "A Very Long Structural Heading That Continues "
    "onto a second visual line without ending"
)
_FRAGMENT = "onto a second visual line without ending"


def test_wrapped_title_pdf_merges_into_single_heading(wrapped_title_pdf, open_doc):
    doc = open_doc(wrapped_title_pdf)
    hs = headings.extract_headings(doc)

    assert len(hs) == 2
    ts = texts(hs)
    # The two visual lines are merged into exactly ONE heading with the full text.
    assert ts == [_MERGED_TITLE, "2 Next Section"]
    assert ts.count(_MERGED_TITLE) == 1

    # NEGATIVE: the wrapped continuation is not emitted as a standalone heading,
    # and no heading is merely the bare first visual line.
    assert _FRAGMENT not in ts
    assert "A Very Long Structural Heading That Continues" not in ts
    # ... but the fragment IS contained within the single merged heading.
    assert _FRAGMENT in hs[0].text

    assert_valid_outline(hs, page_count=doc.page_count, require_monotonic=True)


# --- running_head_pdf ------------------------------------------------------

def test_running_head_pdf_strips_repeated_header(running_head_pdf, open_doc):
    doc = open_doc(running_head_pdf)
    hs = headings.extract_headings(doc)
    ts = texts(hs)

    assert len(hs) == 2
    assert ts == ["1 Real Heading One", "2 Real Heading Two"]
    # real headings land on their true pages (0-based p0 and p2).
    assert [h.page for h in hs] == [0, 2]

    # NEGATIVE: the repeated "Confidential Report Page N" line is stripped for
    # every page number it appeared with.
    for h in hs:
        assert "Confidential Report" not in h.text
    for n in range(1, 5):
        assert f"Confidential Report Page {n}" not in ts

    assert_valid_outline(hs, page_count=doc.page_count, require_monotonic=True)


# --- printed_toc_pdf -------------------------------------------------------

def test_printed_toc_pdf_filters_pagenum_rows(printed_toc_pdf, open_doc):
    doc = open_doc(printed_toc_pdf)
    hs = headings.extract_headings(doc)
    ts = texts(hs)

    assert len(hs) == 2
    assert ts == ["TABLE OF CONTENTS", "1 Actual Heading"]

    # NEGATIVE: printed "NNN - Chapter i" rows (RE_PAGENUM_PREFIX) never surface.
    for i, n in enumerate((6, 44, 63, 81), 1):
        row = f"{n:03d} - Chapter {i}"
        assert row not in ts
    assert not any(" - Chapter " in t for t in ts)

    assert_valid_outline(hs, page_count=doc.page_count, require_monotonic=True)


# --- junk_pdf --------------------------------------------------------------

def test_junk_pdf_keeps_only_genuine_heading(junk_pdf, open_doc):
    doc = open_doc(junk_pdf)
    hs = headings.extract_headings(doc)
    ts = texts(hs)

    assert len(hs) == 1
    assert ts == ["1 Genuine Heading"]

    # NEGATIVE: short junk lines are all rejected (too few letters / bare nums).
    for junk in ("E", "1 0", "7"):
        assert junk not in ts

    assert_valid_outline(hs, page_count=doc.page_count, require_monotonic=True)


# --- body_false_positive_pdf ----------------------------------------------

def test_body_false_positive_pdf_ignores_wide_numbered_body(
    body_false_positive_pdf, open_doc
):
    doc = open_doc(body_false_positive_pdf)
    hs = headings.extract_headings(doc)
    ts = texts(hs)

    assert len(hs) == 1
    assert ts == ["1 The Only Real Heading"]

    # NEGATIVE: the wide, body-size line that merely STARTS with a number is
    # not promoted to a heading (Guard B: numbering needs corroboration).
    assert not any(t.startswith("8 conjugados") for t in ts)
    assert not any("conjugados" in t for t in ts)

    assert_valid_outline(hs, page_count=doc.page_count, require_monotonic=True)
