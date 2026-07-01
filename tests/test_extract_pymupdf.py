"""Integration test: the PyMuPDF adapter over a real, generated PDF.

Builds a small PDF with a large-font heading, body text, bullet lines, and a
recurring footer, then asserts ``extract_pdf_pymupdf`` recovers Markdown
structure and strips the running footer.
"""

from __future__ import annotations

import pytest

fitz = pytest.importorskip("fitz", reason="PyMuPDF not installed")

from ingest import extract


def _make_pdf() -> bytes:
    doc = fitz.open()
    for pageno in range(2):
        page = doc.new_page(width=400, height=600)
        page.insert_text((50, 70), "Formal Statement", fontsize=20)          # heading
        page.insert_text((50, 110), "This is body text that reads normally.", fontsize=11)
        page.insert_text((50, 140), "• first item", fontsize=11)         # bullet
        page.insert_text((50, 165), "• second item", fontsize=11)        # bullet
        page.insert_text((50, 565), f"Report Page {pageno + 1}", fontsize=8)  # footer band
    return doc.tobytes()


def test_pymupdf_extractor_recovers_structure_and_strips_footer():
    ex = extract.extract_pdf_pymupdf(_make_pdf(), fallback_title="synthetic")
    md = ex.full_text

    assert ex.extractor == "pymupdf"
    assert ex.page_count == 2
    # heading recovered from font size
    assert "# Formal Statement" in md
    # body reads normally
    assert "This is body text that reads normally." in md
    # bullets become Markdown list items
    assert "- first item" in md
    assert "- second item" in md
    # the recurring footer is stripped
    assert "Report Page" not in md


def test_dispatch_prefers_pymupdf_for_pdf():
    ex = extract.extract(_make_pdf(), filename="x.pdf", extension=".pdf")
    assert ex.extractor == "pymupdf"
    assert "# Formal Statement" in ex.full_text
