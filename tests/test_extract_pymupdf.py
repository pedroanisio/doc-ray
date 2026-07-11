"""Integration test: the PyMuPDF adapter over a real, generated PDF.

Builds a small PDF with a large-font heading, body text, bullet lines, and a
recurring footer, then asserts ``extract_pdf_pymupdf`` recovers Markdown
structure and strips the running footer.
"""

from __future__ import annotations

import pytest

from doc_ray.adapters import extraction as extract

fitz = pytest.importorskip("fitz", reason="PyMuPDF not installed")


def _make_pdf() -> bytes:
    doc = fitz.open()
    for pageno in range(2):
        page = doc.new_page(width=400, height=600)
        page.insert_text((50, 70), "Formal Statement", fontsize=20)  # heading
        page.insert_text((50, 110), "This is body text that reads normally.", fontsize=11)
        page.insert_text((50, 140), "• first item", fontsize=11)  # bullet
        page.insert_text((50, 165), "• second item", fontsize=11)  # bullet
        page.insert_text((50, 565), f"Report Page {pageno + 1}", fontsize=8)  # footer band
    return doc.tobytes()


def _make_image_only_pdf() -> bytes:
    source = fitz.open()
    page = source.new_page(width=500, height=250)
    page.insert_text((60, 100), "SCANNED OCR TEXT", fontsize=30)
    pixmap = page.get_pixmap(dpi=200)

    image_doc = fitz.open()
    image_page = image_doc.new_page(width=500, height=250)
    image_page.insert_image(image_page.rect, pixmap=pixmap)
    return image_doc.tobytes()


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


def _make_two_page_pdf() -> bytes:
    doc = fitz.open()
    page1 = doc.new_page(width=400, height=600)
    page1.insert_text((50, 100), "Alpha content on page one", fontsize=12)
    page2 = doc.new_page(width=400, height=600)
    page2.insert_text((50, 300), "Beta content on page two", fontsize=12)
    return doc.tobytes()


def test_pymupdf_extractor_surfaces_page_and_bbox_per_run():
    """BL-076: page/bbox geometry is surfaced alongside full_text, unaltered."""
    ex = extract.extract_pdf_pymupdf(_make_two_page_pdf(), fallback_title="synthetic")

    assert "Alpha content on page one" in ex.full_text
    assert "Beta content on page two" in ex.full_text

    assert ex.page_runs, "expected page_runs to be populated"
    alpha = [r for r in ex.page_runs if "Alpha" in r["text"]]
    beta = [r for r in ex.page_runs if "Beta" in r["text"]]
    assert alpha and beta
    assert alpha[0]["page"] == 0
    assert beta[0]["page"] == 1
    # Distinct source pages -> non-overlapping bounding boxes (by construction:
    # the two runs sit at different y-positions on their respective pages).
    a_bbox = alpha[0]["bbox"]
    b_bbox = beta[0]["bbox"]
    assert len(a_bbox) == 4 and len(b_bbox) == 4
    assert (a_bbox[1], a_bbox[3]) != (b_bbox[1], b_bbox[3])


def test_pdf_heading_thresholds_are_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("DOCRAY_PDF_HEADING_L1_RATIO", "1.9")
    monkeypatch.setenv("DOCRAY_PDF_HEADING_L2_RATIO", "1.6")
    monkeypatch.setenv("DOCRAY_PDF_HEADING_L3_RATIO", "1.35")
    monkeypatch.setenv("DOCRAY_PDF_HEADING_BOLD_MAX_WORDS", "4")

    thresholds = extract.pdf_heading_thresholds_from_env()

    assert thresholds.level1_ratio == 1.9
    assert thresholds.level2_ratio == 1.6
    assert thresholds.level3_ratio == 1.35
    assert thresholds.bold_max_words == 4


def test_image_only_pdf_ocr_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("DOCRAY_PDF_OCR", raising=False)

    ex = extract.extract_pdf_pymupdf(_make_image_only_pdf(), fallback_title="scan")

    assert ex.full_text == ""
    assert ex.extractor == "pymupdf"
    assert ex.metadata["ocr_applied"] is False


def test_image_only_pdf_uses_opt_in_ocr(monkeypatch):
    monkeypatch.setenv("DOCRAY_PDF_OCR", "1")

    ex = extract.extract_pdf_pymupdf(_make_image_only_pdf(), fallback_title="scan")

    assert "SCANNED OCR TEXT" in ex.full_text
    assert ex.extractor == "pymupdf+ocr"
    assert ex.metadata["ocr_applied"] is True
