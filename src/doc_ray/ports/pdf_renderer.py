"""Port: render PDF pages to raster images.

Introduced by the Prism visual layer (proposal §5.3). Page rendering previously
lived inline in ``entrypoints/api/downloads.py`` — an entrypoint — which
application/job code must not import from. This port lets both the download
route and the visual-extract job handler depend on rendering through the
hexagonal boundary; the concrete PyMuPDF implementation is an adapter.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class PdfRenderer(Protocol):
    """Render pages of a PDF (given its raw bytes) to PNG raster images."""

    def page_count(self, pdf_bytes: bytes) -> int:
        """Return the number of pages in the PDF."""
        ...

    def render_page_png(self, pdf_bytes: bytes, page_number: int, *, dpi: int) -> bytes:
        """Render one 1-based page to PNG bytes at *dpi*.

        Raises ``IndexError`` when ``page_number`` is out of range.
        """
        ...

    def render_region_png(
        self,
        pdf_bytes: bytes,
        page_number: int,
        bbox: tuple[float, float, float, float],
        *,
        dpi: int,
    ) -> bytes:
        """Render a bounding-box region of one 1-based page to PNG bytes at *dpi*.

        ``bbox`` is ``(x0, y0, x1, y1)`` in PDF points. Raises ``IndexError``
        when ``page_number`` is out of range.
        """
        ...
