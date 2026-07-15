"""The PyMuPDF renderer adapter satisfies the PdfRenderer port (Prism §5.3).

Uses a tiny in-memory PDF built with PyMuPDF so the test needs no fixture file
and stays deterministic. Confirms page counting, page/region PNG output, the
port's structural conformance, and out-of-range handling.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from doc_ray.adapters.pdf_renderer_pymupdf import PyMuPDFRenderer
from doc_ray.ports.pdf_renderer import PdfRenderer


def _two_page_pdf() -> bytes:
    import fitz

    doc = fitz.open()
    for _ in range(2):
        page = doc.new_page(width=200, height=300)
        page.draw_rect(fitz.Rect(20, 20, 180, 280), color=(1, 0, 0), fill=(1, 0, 0))
    data: bytes = doc.tobytes()
    doc.close()
    return data


def test_adapter_conforms_to_the_port() -> None:
    assert isinstance(PyMuPDFRenderer(), PdfRenderer)


def test_page_count() -> None:
    assert PyMuPDFRenderer().page_count(_two_page_pdf()) == 2


def test_render_page_returns_a_png_of_the_page() -> None:
    png = PyMuPDFRenderer().render_page_png(_two_page_pdf(), 1, dpi=72)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    with Image.open(BytesIO(png)) as img:
        # 200x300 pt at 72 dpi ~= 200x300 px.
        assert img.width == 200 and img.height == 300


def test_render_region_is_smaller_than_the_full_page() -> None:
    pdf = _two_page_pdf()
    renderer = PyMuPDFRenderer()
    full = renderer.render_page_png(pdf, 1, dpi=72)
    region = renderer.render_region_png(pdf, 1, (20, 20, 180, 280), dpi=72)
    with Image.open(BytesIO(region)) as img:
        assert img.width < 200 and img.height < 300
    assert len(region) < len(full)


def test_out_of_range_page_raises_indexerror() -> None:
    renderer = PyMuPDFRenderer()
    pdf = _two_page_pdf()
    for bad in (0, 3):
        with pytest.raises(IndexError):
            renderer.render_page_png(pdf, bad, dpi=72)
