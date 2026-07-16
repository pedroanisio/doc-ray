"""Adapter unit tests for :class:`PyMuPDFImageSource`.

Exercise the real PyMuPDF paths (no DB, no object store): embedded-raster
extraction and vector-figure clustering with the calibrated size/aspect filter.
Vector figures on text-heavy pages are the gap the full-page fallback cannot
close (proposal §5.3, Stage 1).
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from doc_ray.adapters.pdf_image_source_pymupdf import PyMuPDFImageSource


def _pdf() -> bytes:
    """A 3-page PDF: (1) a big vector figure + prose, (2) a hairline rule only,
    (3) text only."""
    import fitz

    doc = fitz.open()
    # Page 1: a large filled rectangle (a figure) surrounded by prose.
    page1 = doc.new_page(width=200, height=300)  # area 60000 pt^2
    page1.draw_rect(fitz.Rect(30, 30, 170, 200), color=(0, 0, 1), fill=(0.2, 0.4, 0.9))
    for i in range(6):
        page1.insert_text((20, 215 + i * 12), f"line of body prose number {i} on the page")
    # Page 2: only a hairline rule (thin -> rejected by the aspect/size filter).
    page2 = doc.new_page(width=200, height=300)
    page2.draw_line((10, 150), (190, 151))
    # Page 3: text only, no drawings.
    page3 = doc.new_page(width=200, height=300)
    page3.insert_text((20, 40), "just text, no graphics at all on this page")
    data: bytes = doc.tobytes()
    doc.close()
    return data


def _pdf_with_raster() -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=200, height=300)
    buf = BytesIO()
    Image.new("RGB", (40, 40), (10, 180, 60)).save(buf, "PNG")
    page.insert_image(fitz.Rect(20, 20, 120, 120), stream=buf.getvalue())
    data: bytes = doc.tobytes()
    doc.close()
    return data


def test_vector_figures_detects_drawn_figure_on_text_page() -> None:
    figures = PyMuPDFImageSource().vector_figures(_pdf())
    # Only the page-1 rectangle qualifies; the hairline and the text pages do not.
    assert [f.page_number for f in figures] == [1]
    x0, y0, x1, y1 = figures[0].bbox
    # The cluster bbox brackets the drawn rectangle (allowing for stroke width).
    assert x0 <= 31 and y0 <= 31 and x1 >= 169 and y1 >= 199


def test_vector_figures_skips_text_dense_clusters() -> None:
    """A vector cluster whose area is filled with the page's text is a prose block
    (drawn/outlined), not a figure — the text-coverage guard must drop it."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=200, height=300)
    page.draw_rect(fitz.Rect(20, 20, 180, 260))  # a boxed region...
    for i in range(14):  # ...filled with dense text (a bordered paragraph)
        page.insert_text((26, 34 + i * 16), "the quick brown fox jumps over the lazy dog again")
    data = doc.tobytes()
    doc.close()
    assert PyMuPDFImageSource().vector_figures(data) == []


def test_vector_figures_ignores_text_only_pdf() -> None:
    import fitz

    doc = fitz.open()
    doc.new_page(width=200, height=300).insert_text((20, 40), "text only, no drawings")
    data = doc.tobytes()
    doc.close()
    assert PyMuPDFImageSource().vector_figures(data) == []


def test_embedded_images_returns_placed_raster() -> None:
    images = PyMuPDFImageSource().embedded_images(_pdf_with_raster())
    assert len(images) == 1
    assert images[0].page_number == 1
    assert images[0].width_px == 40 and images[0].height_px == 40
    assert images[0].png_bytes.startswith(b"\x89PNG")
