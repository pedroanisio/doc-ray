"""Shared pytest fixtures for the heading-extraction suite (proto/headings.py).

Self-contained: puts proto/ on sys.path so `import headings` works, and this
dir on sys.path so `import synthpdf` works — regardless of where pytest runs.

Exposes:
  - make_pdf:  factory to build an ad-hoc synthetic PDF from L(...) lines
  - open_doc:  helper to open a PDF as a fitz.Document
  - canonical document fixtures (toc_pdf, numbered_pdf, ...) authors reuse
  - real_corpus: paths to the real static/fixtures/ PDFs (skips if absent)

Do NOT modify synthpdf.py from test modules; import and compose it instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent          # proto/tests
_PROTO = _HERE.parent                            # proto
_REPO = _PROTO.parent                            # repo root
for _p in (str(_PROTO), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from synthpdf import (  # noqa: E402
    L,
    PdfSpec,
    BODY_SIZE,
    body_block,
    build_pdf,
    heading,
    open_doc as _open_doc,
)

CORPUS_DIR = _REPO / "static" / "fixtures"


@pytest.fixture
def make_pdf(tmp_path):
    """Factory: make_pdf([L(...), ...], toc=[[lvl,title,page],...]) -> path str."""
    counter = {"n": 0}

    def _make(lines, toc=None, name=None):
        counter["n"] += 1
        fname = name or f"doc_{counter['n']}.pdf"
        return build_pdf(str(tmp_path / fname), PdfSpec(lines=list(lines), toc=toc))

    return _make


@pytest.fixture
def open_doc():
    return _open_doc


# --- Canonical documents ---------------------------------------------------

@pytest.fixture
def toc_pdf(make_pdf):
    """A PDF carrying an embedded outline spanning multiple pages."""
    lines = (
        body_block(3)
        + [L("Chapter One", 18, True, new_page=True)] + body_block(4)
        + [L("Section 1.1", 14, True, new_page=True)] + body_block(4)
        + [L("Chapter Two", 18, True, new_page=True)] + body_block(4)
    )
    toc = [[1, "Chapter One", 2], [2, "Section 1.1", 3], [1, "Chapter Two", 4]]
    return make_pdf(lines, toc=toc, name="toc.pdf")


@pytest.fixture
def numbered_pdf(make_pdf):
    """No embedded TOC; bold, numbered headings with dot-depth levels."""
    lines = (
        body_block(3)
        + [heading("1 Introduction")] + body_block(5)
        + [heading("1.1 Background")] + body_block(5)
        + [heading("1.1.1 Prior Work")] + body_block(5)
        + [heading("2 Methods")] + body_block(5)
    )
    return make_pdf(lines, name="numbered.pdf")


@pytest.fixture
def wrapped_title_pdf(make_pdf):
    """A heading that wraps across two lines (same style, no sentence end)."""
    lines = (
        [L("A Very Long Structural Heading That Continues", 16, True, gap_before=18),
         L("onto a second visual line without ending", 16, True, gap_before=2)]
        + body_block(6)
        + [heading("2 Next Section")] + body_block(4)
    )
    return make_pdf(lines, name="wrapped.pdf")


@pytest.fixture
def running_head_pdf(make_pdf):
    """4 pages, each with an identical running header (only page number varies)."""
    lines = []
    for p in range(4):
        lines.append(L(f"Confidential Report Page {p + 1}", 10.0,
                       False, new_page=(p > 0)))
        if p == 0:
            lines.append(heading("1 Real Heading One"))
        if p == 2:
            lines.append(heading("2 Real Heading Two"))
        lines += body_block(3, start=p * 10)   # unique body text per page
    return make_pdf(lines, name="runhead.pdf")


@pytest.fixture
def printed_toc_pdf(make_pdf):
    """Body-font 'NNN - Title' rows imitating a printed table of contents."""
    lines = (
        [L("TABLE OF CONTENTS", 12, True, gap_before=10)]
        + [L(f"{n:03d} - Chapter {i}") for i, n in enumerate((6, 44, 63, 81), 1)]
        + [heading("1 Actual Heading", 16, True)] + body_block(5)
    )
    return make_pdf(lines, name="printedtoc.pdf")


@pytest.fixture
def junk_pdf(make_pdf):
    """Short junk lines (single letters, bare numbers) mixed with one heading."""
    lines = (
        body_block(3)
        + [L("E", 11, True, gap_before=8), L("1 0", 11, True, gap_before=8),
           L("7", 20, True, gap_before=8)]
        + [heading("1 Genuine Heading")] + body_block(4)
    )
    return make_pdf(lines, name="junk.pdf")


@pytest.fixture
def body_false_positive_pdf(make_pdf):
    """A wide body line beginning with a number — must NOT become a heading."""
    long_line = ("8 conjugados distintos todos com parte real igual a dez e "
                 "portanto o resultado permanece constante ao longo do texto = 10")
    lines = (
        body_block(3)
        + [L(long_line)]                      # body size, not bold, fills the line
        + body_block(3)
        + [heading("1 The Only Real Heading")] + body_block(3)
    )
    return make_pdf(lines, name="bodyfp.pdf")


# --- Real corpus (optional, tolerant) --------------------------------------

@pytest.fixture(scope="session")
def real_corpus():
    if not CORPUS_DIR.is_dir():
        pytest.skip(f"real corpus not present: {CORPUS_DIR}")
    pdfs = {p.name: p for p in sorted(CORPUS_DIR.glob("*.pdf"))}
    if not pdfs:
        pytest.skip("no PDFs in real corpus")
    return pdfs
