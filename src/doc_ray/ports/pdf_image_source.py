"""Port: extract embedded raster images (with placement geometry) from a PDF.

Prism Tier-1 region detection (proposal §5.3, Stage 1). Embedded rasters are the
``type==1`` blocks the text extractor drops; this port surfaces them with their
native bytes and on-page bounding box so the visual layer can crop, hash, store,
and feature-extract them. Vector-graphics regions are handled separately by
rendering low-text pages through the :class:`PdfRenderer` port.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ExtractedImage:
    """One embedded raster placement on a page."""

    page_number: int  # 1-based
    xref: int
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1) in PDF points
    png_bytes: bytes  # native raster re-encoded as PNG
    width_px: int
    height_px: int


@runtime_checkable
class PdfImageSource(Protocol):
    """Extract embedded raster images from a PDF given its raw bytes."""

    def embedded_images(self, pdf_bytes: bytes) -> list[ExtractedImage]:
        """Return every embedded raster placement, in page then reading order."""
        ...
