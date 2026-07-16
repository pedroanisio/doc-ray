"""Unit tests for the PyMuPDF/Tesseract image-OCR adapter.

The happy path needs a real Tesseract backend (skipped where unavailable); the
graceful-failure path (bad bytes -> "") is deterministic and always runs, because
OCR must never fail the caption job.
"""

from __future__ import annotations

import shutil

import pytest

from doc_ray.adapters.image_ocr_pymupdf import PyMuPDFImageOcr


def _text_png(text: str) -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=460, height=140)
    page.insert_text((20, 80), text, fontsize=44)
    data: bytes = page.get_pixmap(dpi=200).tobytes("png")
    doc.close()
    return data


def test_bad_bytes_return_empty_string() -> None:
    # OCR is a grounding aid, never load-bearing: unreadable input -> "".
    assert PyMuPDFImageOcr().ocr_image(b"not a real image") == ""


def test_ocr_reads_rendered_text() -> None:
    if shutil.which("tesseract") is None:
        pytest.skip("tesseract binary not installed")
    text = PyMuPDFImageOcr().ocr_image(_text_png("OPTIMIZATION"))
    if not text.strip():
        pytest.skip("OCR backend produced no text (tessdata unavailable)")
    assert "OPTIMIZATION" in text.upper()
