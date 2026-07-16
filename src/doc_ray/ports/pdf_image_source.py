"""Port: detect image regions (with placement geometry) in a PDF.

Prism Tier-1 region detection (proposal §5.3, Stage 1). Two deterministic
sources:

* **Embedded rasters** — the ``type==1`` blocks the text extractor drops; this
  port surfaces them with their native bytes and on-page bounding box so the
  visual layer can crop, hash, store, and feature-extract them.
* **Vector-graphics figures** — diagrams/schematics drawn as vector paths (not
  rasters) carry meaning the raster path never sees. ``fitz`` clusters vector
  drawings into figure bounding boxes; this port surfaces the figure-sized
  clusters so the renderer can rasterize each region. This closes the "vector
  figures on text-heavy pages" gap that the full-page-render fallback (which
  only fires for near-text-free pages) cannot: a labelled diagram sitting in a
  page of prose is invisible to both the raster extractor and the page-render
  heuristic.
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


@dataclass(frozen=True)
class VectorFigure:
    """One vector-graphics figure region: a cluster of vector drawings.

    Carries only page + geometry; the bytes are produced later by rendering the
    region through the :class:`~doc_ray.ports.pdf_renderer.PdfRenderer` port, so
    the resolution is a render-time decision, not baked into detection.
    """

    page_number: int  # 1-based
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1) in PDF points


@runtime_checkable
class PdfImageSource(Protocol):
    """Detect image regions in a PDF given its raw bytes."""

    def embedded_images(self, pdf_bytes: bytes) -> list[ExtractedImage]:
        """Return every embedded raster placement, in page then reading order."""
        ...

    def vector_figures(self, pdf_bytes: bytes) -> list[VectorFigure]:
        """Return figure-sized vector-drawing clusters, in page then reading order."""
        ...
