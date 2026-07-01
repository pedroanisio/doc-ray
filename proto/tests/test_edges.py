"""Edge-case robustness: empty documents and large-outline truncation."""

from __future__ import annotations

from pathlib import Path

import headings
from synthpdf import PdfSpec, build_pdf, body_block, heading


def test_empty_pdf_yields_no_headings(make_pdf, open_doc):
    """A blank page (no text at all) must return [] without raising."""
    path = make_pdf([])                       # build_pdf still emits one blank page
    doc = open_doc(path)
    assert doc.page_count == 1
    assert headings.extract_headings(doc) == []


def test_body_only_pdf_yields_no_headings(make_pdf, open_doc):
    """Prose with no distinctive lines produces an empty outline."""
    doc = open_doc(make_pdf(body_block(20)))
    assert headings.extract_headings(doc) == []


def _alpha(n: int) -> str:
    """0->a, 1->b, ... 25->z, 26->aa — distinct *alphabetic* labels so that
    the digit-normalizing running-head stripper can't collapse them."""
    s, n = "", n + 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(97 + r) + s
    return s


def test_run_truncates_long_outline(tmp_path, capsys):
    """run() prints only the first 60 headings and a '... +N more' footer."""
    lines = []
    for i in range(1, 71):                    # 70 headings, each unique by word
        lines.append(heading(f"{i} Heading {_alpha(i)}"))
        lines += body_block(2, start=i * 3)
    path = str(tmp_path / "many.pdf")
    build_pdf(path, PdfSpec(lines=lines))

    headings.run(Path(path), debug=False)
    out = capsys.readouterr().out
    assert "... +" in out and "more" in out   # truncation footer present
    assert "70 headings" in out               # header reports the true total
