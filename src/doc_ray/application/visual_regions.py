"""Prism Tier-1 region planning (proposal §5.3, Stage 1).

Decides which image regions a document contributes, from two deterministic
sources:

* **Embedded rasters** — every ``ExtractedImage`` the image-source adapter finds.
* **Full-page renders of near-text-free pages** — because ``fitz`` undercounts
  vector graphics, a page with (almost) no text runs and no embedded raster is
  most likely a vector schematic/diagram carrying the page's meaning; render it
  whole so the visual layer still sees it. Pages that already carry an embedded
  raster are not double-counted.

This module is pure/deterministic and takes a ``render_full_page`` callable, so
it unit-tests with fakes — no PDF engine, no DB.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from doc_ray.ports.pdf_image_source import ExtractedImage

SOURCE_EMBEDDED = "embedded-raster"
SOURCE_FULL_PAGE = "full-page"

#: A page with at most this many text runs is treated as visual (vector-dominant).
LOW_TEXT_RUN_THRESHOLD = 3


@dataclass(frozen=True)
class PlannedRegion:
    """One crop to feature-extract: its provenance, geometry, and PNG bytes."""

    page_number: int
    source: str
    bbox: tuple[float, float, float, float] | None
    xref: int | None
    png_bytes: bytes
    width_px: int
    height_px: int


def plan_regions(
    embedded: list[ExtractedImage],
    page_text_runs: Mapping[int, int],
    render_full_page: Callable[[int], tuple[bytes, int, int]],
) -> list[PlannedRegion]:
    """Plan the crops for a document.

    Args:
        embedded: Embedded raster placements from the image-source adapter.
        page_text_runs: ``page_number -> text_run_count`` (from ``document_pages``).
        render_full_page: ``page_number -> (png_bytes, width_px, height_px)``.
    """
    regions: list[PlannedRegion] = [
        PlannedRegion(
            page_number=img.page_number,
            source=SOURCE_EMBEDDED,
            bbox=img.bbox,
            xref=img.xref,
            png_bytes=img.png_bytes,
            width_px=img.width_px,
            height_px=img.height_px,
        )
        for img in embedded
    ]
    pages_with_embedded = {img.page_number for img in embedded}
    for page_number, runs in sorted(page_text_runs.items()):
        if runs <= LOW_TEXT_RUN_THRESHOLD and page_number not in pages_with_embedded:
            png, width, height = render_full_page(page_number)
            regions.append(
                PlannedRegion(
                    page_number=page_number,
                    source=SOURCE_FULL_PAGE,
                    bbox=None,
                    xref=None,
                    png_bytes=png,
                    width_px=width,
                    height_px=height,
                )
            )
    return regions
