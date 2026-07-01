"""Tests for headings.strip_running_heads(lines, n_pages).

The stripper removes lines that repeat across enough pages to look like a
running header/footer. Its signature normalises digits to '#' and lowercases,
so page-number-only differences collapse to one signature. A line is dropped
when its signature count reaches threshold = max(3, 0.4 * n_pages), and the
whole stage is a no-op when n_pages < 3.
"""

from __future__ import annotations

import re

import headings
from synthpdf import L, PdfSpec, build_pdf, body_block, heading, open_doc


def _norm(text: str) -> str:
    """Reproduce the stripper's signature normalisation for assertions."""
    return re.sub(r"\d+", "#", text.lower())


_LETTERS = "abcdefghijklmnopqrstuvwxyz"


def _tag(page: int) -> str:
    """A per-page suffix using only LETTERS (no digits), so distinct body
    lines keep distinct signatures under the digit->'#' normalisation."""
    return _LETTERS[page % 26] * 3  # 'aaa', 'bbb', ...


def _line(text: str, page: int, size: float = 10.0, bold: bool = False) -> headings.Line:
    """A minimal Line on a given page; geometry is irrelevant to the stripper."""
    return headings.Line(
        text=text,
        size=size,
        bold=bold,
        font="helv",
        page=page,
        y0=72.0,
        y1=82.0,
        x0=72.0,
        x1=300.0,
    )


# --- Integration against the canonical running_head_pdf fixture ------------

def test_running_head_pdf_strips_repeated_header_keeps_real_headings(
    running_head_pdf, open_doc
):
    """From the real fixture: extract lines, then strip. The 4x repeated
    'Confidential Report Page N' header must vanish while both genuine
    headings survive."""
    doc = open_doc(running_head_pdf)
    assert doc.page_count == 4

    raw = headings.extract_lines(doc)
    stripped = headings.strip_running_heads(raw, doc.page_count)

    # Sanity: the header genuinely was present before stripping (so the test
    # is not vacuously true) and it repeats across the pages.
    raw_norms = [_norm(ln.text) for ln in raw]
    assert raw_norms.count("confidential report page #") == 4

    surviving_norms = {_norm(ln.text) for ln in stripped}
    assert "confidential report page #" not in surviving_norms

    surviving_texts = {ln.text for ln in stripped}
    assert "1 Real Heading One" in surviving_texts
    assert "2 Real Heading Two" in surviving_texts

    # Only the 4 header instances are removed; everything else is retained.
    assert len(stripped) == len(raw) - 4


# --- Unit: repeated header across 5 pages is stripped ----------------------

def test_header_repeated_every_page_is_stripped():
    """A header that appears on all 5 pages (only its page number varies)
    normalises to a single signature with count 5 >= threshold(=3), so every
    instance is removed. A distinct body line on each page survives."""
    lines = []
    for p in range(5):
        lines.append(_line(f"Confidential Report Page {p + 1}", p))
        lines.append(_line(f"Distinct paragraph text on page {_tag(p)}", p))

    out = headings.strip_running_heads(lines, 5)
    out_norms = [_norm(ln.text) for ln in out]

    # threshold = max(3, 0.4 * 5) = 3; header count 5 >= 3 -> stripped.
    assert "confidential report page #" not in out_norms
    # All 5 unique body lines (distinct by non-digit letters) remain.
    assert len(out) == 5
    assert all(ln.text.startswith("Distinct paragraph") for ln in out)


def test_line_on_two_of_five_pages_is_kept():
    """A line appearing on only 2 of 5 pages has count 2 < threshold(=3),
    so it must NOT be stripped even though a genuine header alongside it is."""
    lines = []
    for p in range(5):
        lines.append(_line(f"Confidential Report Page {p + 1}", p))  # 5x -> stripped
        if p < 2:
            lines.append(_line("Appears On Two Pages", p))            # 2x -> kept
        lines.append(_line(f"unique body content on page {_tag(p)}", p))

    out = headings.strip_running_heads(lines, 5)
    out_norms = [_norm(ln.text) for ln in out]

    assert "appears on two pages" in out_norms          # under-threshold survives
    assert "confidential report page #" not in out_norms  # over-threshold removed
    # Kept: 2 instances of the two-page line + 5 unique body lines = 7.
    assert out_norms.count("appears on two pages") == 2
    assert len(out) == 7


def test_threshold_is_exactly_max_three_and_point_four_n():
    """Boundary check: with 10 pages threshold = max(3, 4.0) = 4. A line on
    exactly 3 pages is KEPT (3 < 4); a line on exactly 4 pages is STRIPPED
    (4 >= 4). This pins the threshold formula, not just 'repeats a lot'."""
    filler = [_line(f"body line variation on page {_tag(p)}", p) for p in range(10)]

    three = [_line("Header On Three", p) for p in range(3)] + list(filler)
    out_three = headings.strip_running_heads(three, 10)
    assert "header on three" in [_norm(ln.text) for ln in out_three]

    four = [_line("Header On Four", p) for p in range(4)] + list(filler)
    out_four = headings.strip_running_heads(four, 10)
    assert "header on four" not in [_norm(ln.text) for ln in out_four]


def test_digits_normalised_so_varying_page_numbers_collapse():
    """Headers differing only in digits must be treated as one signature.
    'Page 1'..'Page 4' collapse to 'page #' (count 4 >= threshold 3) and are
    all stripped; a header with differing NON-digit words would not."""
    same_sig = [_line(f"Page {p + 1}", p) for p in range(4)]  # digits vary only
    diff_sig = [_line(f"Section {chr(ord('A') + p)}", p) for p in range(4)]  # letters vary
    lines = same_sig + diff_sig

    out = headings.strip_running_heads(lines, 4)
    out_norms = [_norm(ln.text) for ln in out]

    # threshold = max(3, 0.4 * 4) = 3.
    assert "page #" not in out_norms            # all four "Page N" removed
    # The letter-varying lines each have a UNIQUE signature (count 1) -> kept.
    assert len(out) == 4
    assert all(ln.text.startswith("Section ") for ln in out)


# --- No-op guard: n_pages < 3 returns the list unchanged -------------------

def test_noop_when_fewer_than_three_pages_returns_same_list_object():
    """With n_pages < 3 the function short-circuits and returns the SAME list
    object it was given — even a footer repeated on both pages is retained."""
    lines = [
        _line("Repeated Footer", 0),
        _line("Some page-zero body text here uno", 0),
        _line("Repeated Footer", 1),
        _line("Some page-one body text here dos", 1),
    ]
    out = headings.strip_running_heads(lines, 2)

    assert out is lines                       # unchanged, not a filtered copy
    assert [ln.text for ln in out].count("Repeated Footer") == 2


def test_noop_two_pages_keeps_heavily_repeated_line():
    """Even a line repeated far above any per-count threshold is kept when
    n_pages < 3, because the whole stage is skipped."""
    lines = [_line("Repeated Footer", p % 2) for p in range(12)]
    out = headings.strip_running_heads(lines, 2)

    assert len(out) == 12
    assert all(ln.text == "Repeated Footer" for ln in out)


def test_three_pages_is_not_noop_and_strips():
    """n_pages == 3 is the first page count where stripping activates
    (threshold max(3, 1.2) = 3), so a 3x-repeated header is removed — proving
    the boundary sits at n_pages < 3, not <= 3."""
    lines = []
    for p in range(3):
        lines.append(_line("Running Header Line", p))
        lines.append(_line(f"body prose distinct on page {_tag(p)}", p))

    out = headings.strip_running_heads(lines, 3)
    out_norms = [_norm(ln.text) for ln in out]

    assert "running header line" not in out_norms
    assert len(out) == 3  # only the three unique body lines remain
