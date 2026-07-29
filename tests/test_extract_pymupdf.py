"""Integration test: the PyMuPDF adapter over a real, generated PDF.

Builds a small PDF with a large-font heading, body text, bullet lines, and a
recurring footer, then asserts ``extract_pdf_pymupdf`` recovers Markdown
structure and strips the running footer.
"""

from __future__ import annotations

import pytest

from doc_ray.adapters import extraction as extract
from doc_ray.adapters import extraction_pdf as extract_pdf_mod
from doc_ray.domain import progress as domain_progress

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


def _make_blank_then_text_pdf() -> bytes:
    doc = fitz.open()
    doc.new_page(width=320, height=480)
    page2 = doc.new_page(width=640, height=360)
    page2.insert_text((50, 120), "Text appears only on page two", fontsize=12)
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


def test_pymupdf_extractor_surfaces_every_page_dimension_even_without_text():
    ex = extract.extract_pdf_pymupdf(_make_blank_then_text_pdf(), fallback_title="synthetic")

    assert ex.page_count == 2
    assert ex.page_metrics == [
        {
            "page": 0,
            "page_width": 320.0,
            "page_height": 480.0,
            "has_text": False,
            "text_run_count": 0,
        },
        {
            "page": 1,
            "page_width": 640.0,
            "page_height": 360.0,
            "has_text": True,
            "text_run_count": 1,
        },
    ]


def test_pymupdf_extractor_reports_progress_per_page():
    events: list[tuple[int | None, int | None, str | None]] = []
    token = domain_progress.set_progress_reporter(
        lambda current, total, stage: events.append((current, total, stage))
    )
    try:
        extract.extract_pdf_pymupdf(_make_two_page_pdf(), fallback_title="synthetic")
    finally:
        domain_progress.reset_progress_reporter(token)

    assert events == [
        (0, 2, "Extracting PDF page 1 of 2"),
        (1, 2, "Extracting PDF page 2 of 2"),
    ]


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


def test_image_only_pdf_is_ocred_by_default(monkeypatch):
    """A scan must ingest out of the box.

    OCR used to require ``DOCRAY_PDF_OCR``, and unset is what every deployment
    ran: a scanned PDF became ``status='failed'`` advising OCR that the product
    would not perform. The pass only fires when the layout extractor read *no*
    text, so the alternative to running it is a failed ingest, not a cheap one.
    """
    monkeypatch.delenv("DOCRAY_PDF_OCR", raising=False)

    ex = extract.extract_pdf_pymupdf(_make_image_only_pdf(), fallback_title="scan")

    assert "SCANNED OCR TEXT" in ex.full_text
    assert ex.extractor == "pymupdf+ocr"
    assert ex.metadata["ocr_applied"] is True


def test_image_only_pdf_ocr_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("DOCRAY_PDF_OCR", "0")

    ex = extract.extract_pdf_pymupdf(_make_image_only_pdf(), fallback_title="scan")

    assert ex.full_text == ""
    assert ex.extractor == "pymupdf"
    assert ex.metadata["ocr_applied"] is False


def test_ocr_language_is_configurable(monkeypatch):
    monkeypatch.setenv("DOCRAY_PDF_OCR", "1")
    monkeypatch.setenv("DOCRAY_PDF_OCR_LANGUAGE", "eng")

    ex = extract.extract_pdf_pymupdf(_make_image_only_pdf(), fallback_title="scan")

    assert ex.metadata["ocr_language"] == "eng"


def test_a_failing_ocr_pass_is_reported_not_swallowed(monkeypatch):
    """A missing Tesseract must name itself, not masquerade as an unreadable file.

    ``extract_pdf`` catches everything from the PyMuPDF path and retries with
    pypdf. For an image-only PDF that fallback also reads zero characters, so a
    broken OCR install surfaced as "no extractable text — OCR required": advice
    to do the very thing that had just failed, with no trace of the real fault.
    """

    def boom(*_args, **kwargs):
        if kwargs.get("ocr"):
            raise RuntimeError("tesseract not found")
        return []

    monkeypatch.delenv("DOCRAY_PDF_OCR", raising=False)
    monkeypatch.setattr(extract_pdf_mod, "_blocks_from_pages", boom)

    with pytest.raises(extract.OcrUnavailableError) as excinfo:
        extract.extract(_make_image_only_pdf(), filename="scan", extension=".pdf")

    assert "tesseract not found" in str(excinfo.value)
    assert "Tesseract installed" in str(excinfo.value)


def test_tessdata_prefix_is_discovered_when_unset(tmp_path, monkeypatch):
    """PyMuPDF's OCR needs TESSDATA_PREFIX; the image must not have to hardcode it."""
    tessdata = tmp_path / "usr" / "share" / "tesseract-ocr" / "5" / "tessdata"
    tessdata.mkdir(parents=True)
    monkeypatch.setattr(
        extract_pdf_mod, "_TESSDATA_GLOBS", (str(tmp_path / "usr/share/tesseract-ocr/*/tessdata"),)
    )

    env: dict[str, str] = {}
    assert extract_pdf_mod.ensure_tessdata_prefix(env) == str(tessdata)
    assert env["TESSDATA_PREFIX"] == str(tessdata)

    configured = {"TESSDATA_PREFIX": "/operator/choice"}
    assert extract_pdf_mod.ensure_tessdata_prefix(configured) == "/operator/choice"
