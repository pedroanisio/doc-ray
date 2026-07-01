"""Deterministic synthetic-PDF builder for heading-extraction tests.

Produces PDFs with fully controlled typography (size, weight), vertical
layout (gaps), pagination, and optional embedded outlines (TOC). This keeps
the test suite hermetic — it never depends on the real, shifting corpus in
static/fixtures/.

Coordinate model: PyMuPDF's insert_text places the text *baseline* at the
given point, with y growing downward from the page top.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import fitz  # PyMuPDF

PAGE_W, PAGE_H = 595.0, 842.0   # A4 points
MARGIN = 72.0
BODY_SIZE = 10.0
LEADING_FACTOR = 1.3            # baseline-to-baseline spacing = size * this

# Base-14 font names: Helvetica / Helvetica-Bold.
FONT_REGULAR = "helv"
FONT_BOLD = "hebo"


@dataclass
class L:
    """One text line to render."""
    text: str
    size: float = BODY_SIZE
    bold: bool = False
    gap_before: float = 0.0     # EXTRA space above this line (points)
    new_page: bool = False      # start a fresh page before rendering this line
    x: float = MARGIN           # left x of the baseline point


@dataclass
class PdfSpec:
    lines: list[L] = field(default_factory=list)
    toc: list | None = None     # e.g. [[level, title, page1based], ...]


def build_pdf(path: str, spec: PdfSpec) -> str:
    """Render `spec` to a PDF at `path`. Returns `path`."""
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    y = MARGIN
    for ln in spec.lines:
        if ln.new_page:
            page = doc.new_page(width=PAGE_W, height=PAGE_H)
            y = MARGIN
        y += ln.gap_before
        # Advance by a normal leading BEFORE drawing so gap_before is additive
        # on top of the natural line spacing.
        y += ln.size * LEADING_FACTOR
        if y > PAGE_H - MARGIN:      # auto-overflow to next page
            page = doc.new_page(width=PAGE_W, height=PAGE_H)
            y = MARGIN + ln.size * LEADING_FACTOR
        font = FONT_BOLD if ln.bold else FONT_REGULAR
        page.insert_text((ln.x, y), ln.text, fontsize=ln.size, fontname=font)
    if spec.toc:
        doc.set_toc(spec.toc)
    doc.save(path)
    doc.close()
    return path


# --- Convenience builders for common line kinds ---------------------------

_WORDS = (
    "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod "
    "tempor incididunt labore magna aliqua enim minim veniam quis nostrud "
    "exercitation ullamco laboris nisi aliquip commodo consequat duis aute "
    "irure reprehenderit voluptate velit esse cillum fugiat nulla pariatur"
).split()


def _uniq_token(n: int) -> str:
    """0->'wa', 1->'wb', ... a purely-alphabetic label unique per n, so the
    digit-normalising running-head stripper can never collapse two body lines."""
    s, n = "", n + 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(97 + r) + s
    return "w" + s


def body_block(n: int, start: int = 0, prefix: str = "The") -> list[L]:
    """`n` lines of ordinary body text (establishes the modal body size).

    Each line carries a UNIQUE alphabetic token plus varied filler, so the
    running-head stripper — which normalises digits to '#' — never mistakes
    repeated body prose for a running header even across hundreds of lines.
    `start` offsets the token/word window so multi-page docs stay unique.
    """
    out: list[L] = []
    w = len(_WORDS)
    for i in range(n):
        k = start + i
        words = [_WORDS[(k * 5 + j) % w] for j in range(8)]
        out.append(L(f"{prefix} {_uniq_token(k)} {' '.join(words)} paragraph."))
    return out


def heading(text: str, size: float = 16.0, bold: bool = True,
            gap: float = 18.0) -> L:
    return L(text, size=size, bold=bold, gap_before=gap)


def open_doc(path: str) -> fitz.Document:
    return fitz.open(path)
