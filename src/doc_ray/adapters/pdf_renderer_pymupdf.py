"""Adapter: PyMuPDF (fitz) implementation of the :class:`PdfRenderer` port.

Lifts the page-render logic that previously lived in
``entrypoints/api/downloads.py`` behind the hexagonal boundary so both the
download route and the visual-extract job handler share one renderer (Prism
§5.3). Blocking/CPU-bound; callers run it off the event loop as needed.
"""

from __future__ import annotations

from typing import cast

from doc_ray.ports.pdf_renderer import PdfRenderer

#: Default render resolution — matches the existing page-image cache in
#: ``downloads.py`` so a migrated download path is byte-compatible.
DEFAULT_RENDER_DPI = 144


class PyMuPDFRenderer(PdfRenderer):
    """Render PDF pages/regions to PNG via PyMuPDF."""

    def page_count(self, pdf_bytes: bytes) -> int:
        """Return the number of pages in the PDF."""
        import fitz

        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            return int(doc.page_count)

    def render_page_png(
        self, pdf_bytes: bytes, page_number: int, *, dpi: int = DEFAULT_RENDER_DPI
    ) -> bytes:
        """Render one 1-based page to PNG bytes at *dpi* (IndexError if out of range)."""
        import fitz

        if page_number < 1:
            raise IndexError(page_number)
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            if page_number > doc.page_count:
                raise IndexError(page_number)
            page = doc.load_page(page_number - 1)
            pixmap = page.get_pixmap(dpi=dpi, alpha=False)
            return cast(bytes, pixmap.tobytes("png"))

    def render_region_png(
        self,
        pdf_bytes: bytes,
        page_number: int,
        bbox: tuple[float, float, float, float],
        *,
        dpi: int = DEFAULT_RENDER_DPI,
    ) -> bytes:
        """Render a ``(x0,y0,x1,y1)``-point region of a 1-based page to PNG bytes."""
        import fitz

        if page_number < 1:
            raise IndexError(page_number)
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            if page_number > doc.page_count:
                raise IndexError(page_number)
            page = doc.load_page(page_number - 1)
            clip = fitz.Rect(*bbox)
            pixmap = page.get_pixmap(dpi=dpi, alpha=False, clip=clip)
            return cast(bytes, pixmap.tobytes("png"))
