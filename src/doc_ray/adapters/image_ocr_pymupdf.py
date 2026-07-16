"""Adapter: PyMuPDF/Tesseract implementation of the :class:`ImageOcr` port.

Reuses the same PyMuPDF OCR backend the PDF extractor uses
(``page.get_textpage_ocr``), applied to a standalone raster crop opened as a
one-page document. OCR is a *grounding aid*, never load-bearing: any failure
(no Tesseract, corrupt bytes, unsupported image) returns an empty string, which
simply means the caption gets less corroboration and the gate stays conservative
— the safe direction under PALS.
"""

from __future__ import annotations

import os
from typing import cast

DEFAULT_OCR_LANGUAGE = "eng"
#: Re-render DPI for OCR. Crops are stored at render DPI (144); OCR reads small
#: labels more reliably when the page is rasterized a little higher.
DEFAULT_OCR_DPI = 200


class PyMuPDFImageOcr:
    """OCR a raster crop's visible text via PyMuPDF + Tesseract."""

    def __init__(self, language: str | None = None, dpi: int = DEFAULT_OCR_DPI) -> None:
        """Configure the Tesseract language (defaults to DOCRAY_VLM_OCR_LANGUAGE or 'eng')."""
        self.language = (
            language or os.environ.get("DOCRAY_VLM_OCR_LANGUAGE", DEFAULT_OCR_LANGUAGE)
        ).strip() or DEFAULT_OCR_LANGUAGE
        self.dpi = dpi

    def ocr_image(self, image_bytes: bytes) -> str:
        """Return the OCR-read text of the crop, or '' if none/on any failure."""
        import fitz

        try:
            with fitz.open(stream=image_bytes, filetype="png") as doc:
                if doc.page_count == 0:
                    return ""
                page = doc.load_page(0)
                textpage = page.get_textpage_ocr(language=self.language, full=True, dpi=self.dpi)
                text = cast(str, page.get_text(textpage=textpage)) or ""
        except Exception:  # noqa: BLE001 — OCR is a grounding aid; never fail the caption job
            return ""
        return text.strip()
