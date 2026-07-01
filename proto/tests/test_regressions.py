"""Regression tests: one test per FIXED bug in proto/headings.py.

Each test pins a specific defect that was fixed and is written so that it would
FAIL again if that fix regressed. They combine the canonical fixtures (which
carry the real-world failure shapes) with small, hand-built unit inputs that
exercise the exact guard/branch responsible for the fix.
"""

from __future__ import annotations

import re

import headings
from synthpdf import L, body_block, heading
from invariants import assert_valid_outline, texts


# --- Bug 1: bare numbering must not, on its own, promote a body line -------
def test_numbering_requires_corroboration(body_false_positive_pdf, make_pdf, open_doc):
    """Numbering alone (RE_DOTNUM match) is a WEAK signal.

    Regression guard for `num_strong` in extract_headings: a numbered line is
    only kept when short AND corroborated by bold/gap/larger size. Two ways it
    can regress -> two independent assertions:

    (a) The wide body sentence '8 conjugados ... = 10' begins with a digit and
        matches RE_DOTNUM, yet fills the column and is body-size: it must be
        rejected. If the corroboration requirement were dropped it would leak
        in as a heading.
    (b) A SHORT numbered line that is body-size, not bold, with no gap has the
        `num` and `short` signals but NO corroboration -> it must NOT be
        emitted. (A tautology-proof case: numbering + short is present, so a
        naive rule keying on those alone would wrongly emit it.)
    """
    # (a) the corpus false-positive line is absent; only the real heading remains.
    doc = open_doc(body_false_positive_pdf)
    hs = headings.extract_headings(doc)
    out = texts(hs)
    assert out == ["1 The Only Real Heading"], out
    assert not any(t.startswith("8 conjugados") for t in out), out
    assert not any("conjugados" in t for t in out), out

    # (b) unit: a short, body-size, non-bold, gap-less numbered line.
    #     body_block establishes the modal body size (10) and a wide column so
    #     the short numbered line reads as `short` but lacks corroboration.
    weak = L("1 Weak Numbered Line", size=10.0, bold=False, gap_before=0.0)
    real = heading("2 Corroborated Heading")  # size 16, bold, gap -> strong
    doc2 = open_doc(make_pdf(body_block(4) + [weak] + body_block(4) + [real]))
    out2 = texts(headings.extract_headings(doc2))
    # The corroborated control DOES appear (proves the pipeline can emit
    # numbered headings at all -> the weak line's absence is meaningful).
    assert "2 Corroborated Heading" in out2, out2
    assert "1 Weak Numbered Line" not in out2, out2


# --- Bug 2: printed table-of-contents rows must be filtered ----------------
def test_printed_toc_rows_filtered(printed_toc_pdf, open_doc):
    """Rows like '006 - Chapter 1' (RE_PAGENUM_PREFIX) are dropped.

    Regression guard for Guard D in extract_headings. The printed-TOC rows are
    body-size 'NNN - Title' lines; every one matches RE_PAGENUM_PREFIX and must
    be filtered out, while the genuine '1 Actual Heading' survives.
    """
    doc = open_doc(printed_toc_pdf)
    hs = headings.extract_headings(doc)
    out = texts(hs)

    # Sanity: the regex actually matches the printed-row shape (so the assertion
    # below is testing a live filter, not a dead one).
    assert headings.RE_PAGENUM_PREFIX.match("006 - Chapter 1")

    assert out == ["TABLE OF CONTENTS", "1 Actual Heading"], out
    for t in out:
        assert headings.RE_PAGENUM_PREFIX.match(t) is None, f"leaked printed-TOC row: {t!r}"
    # Concretely: none of the numeric-prefixed rows leaked through.
    for n in (6, 44, 63, 81):
        assert f"{n:03d} - Chapter" not in " || ".join(out), out


# --- Bug 3: junk floor drops sub-3-letter lines ----------------------------
def test_junk_floor(junk_pdf, open_doc):
    """Lines with fewer than 3 letters are rejected (Guard A).

    'E', '1 0', '7' each have < 3 alphabetic characters and must be absent from
    the output, even though they are bold / large / gap-preceded (typical
    heading typography). Only '1 Genuine Heading' survives.
    """
    doc = open_doc(junk_pdf)
    hs = headings.extract_headings(doc)
    out = texts(hs)

    assert out == ["1 Genuine Heading"], out
    for junk in ("E", "1 0", "7"):
        assert junk not in out, f"junk leaked: {junk!r}"
        # The reason they are dropped: fewer than 3 letters.
        assert headings._letters(junk) < 3, (junk, headings._letters(junk))
    # Control: the real heading clears the floor.
    assert headings._letters("1 Genuine Heading") >= 3


# --- Bug 4: a wrapped title is merged into one heading ---------------------
def test_wrapped_title_merged(wrapped_title_pdf, open_doc):
    """A heading that wrapped onto a second visual line becomes ONE heading.

    Regression guard for _merge_wrapped: the two same-style, tightly-spaced
    lines (first not sentence-terminated, second not itself numbered) collapse
    into a single merged heading. Neither fragment may appear on its own.
    """
    doc = open_doc(wrapped_title_pdf)
    hs = headings.extract_headings(doc)
    out = texts(hs)

    full = ("A Very Long Structural Heading That Continues "
            "onto a second visual line without ending")
    assert out == [full, "2 Next Section"], out

    # Exactly one heading equals the merged full title...
    assert out.count(full) == 1, out
    # ...and neither raw fragment leaked as its own heading.
    assert "A Very Long Structural Heading That Continues" not in out, out
    assert "onto a second visual line without ending" not in out, out
    # The merged heading carries the wrapped-title confidence (0.6), distinct
    # from the numbered heading (0.9) -> proves it went through the merge path.
    merged = next(h for h in hs if h.text == full)
    assert merged.confidence == 0.6, merged.confidence


# --- Bug 5: an embedded TOC takes the fast path over heuristics ------------
def test_toc_fastpath_preferred(toc_pdf, open_doc):
    """When a multi-level embedded outline exists, heuristics are skipped.

    Regression guard for the fast path at the top of extract_headings. Every
    emitted heading must have confidence 1.0 and signals {'source': 'toc'} —
    the tell-tale of the TOC path — and the outline must match the author's
    verbatim (page_count monotonic check relaxed, as the invariant allows).
    """
    doc = open_doc(toc_pdf)
    hs = headings.extract_headings(doc)

    assert texts(hs) == ["Chapter One", "Section 1.1", "Chapter Two"], texts(hs)
    for h in hs:
        assert h.confidence == 1.0, (h.text, h.confidence)
        assert h.signals.get("source") == "toc", (h.text, h.signals)
        # Heuristic-only signal keys (e.g. 'size_ratio', 'bold', 'num') must be
        # absent -> confirms score_line never ran for these headings.
        assert "size_ratio" not in h.signals, h.signals
        assert "num" not in h.signals, h.signals
    # The fast path trusts the author's nesting verbatim (levels 1,2,1).
    assert [h.level for h in hs] == [1, 2, 1], [h.level for h in hs]
    assert_valid_outline(hs, page_count=doc.page_count, require_monotonic=False)
