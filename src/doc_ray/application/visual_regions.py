"""Prism Tier-1 region planning (proposal §5.3, Stage 1).

Decides which image regions a document contributes, from three deterministic
sources:

* **Embedded rasters** — every ``ExtractedImage`` the image-source adapter finds.
* **Vector-graphics figures** — every ``VectorFigure`` cluster the image-source
  adapter finds, rasterized as a ``rendered-region`` crop. This captures
  diagrams/schematics drawn as vector paths that sit on otherwise text-heavy
  pages (a labelled figure in a page of prose), which neither the raster
  extractor nor the full-page fallback can see.
* **Full-page renders of near-text-free pages** — a page with (almost) no text
  runs and no embedded raster is most likely a full-page vector schematic;
  render it whole. Pages that already carry an embedded raster are not
  double-counted.

Vector figures are de-duplicated against the other two sources: a figure that
overlaps an embedded raster (a diagram that is itself a placed image) or sits on
a page rendered whole is dropped, so a region is never captured twice.

This module is pure/deterministic and takes ``render_full_page`` /
``render_region`` callables, so it unit-tests with fakes — no PDF engine, no DB.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from doc_ray.ports.pdf_image_source import ExtractedImage, VectorFigure

SOURCE_EMBEDDED = "embedded-raster"
SOURCE_RENDERED_REGION = "rendered-region"
SOURCE_FULL_PAGE = "full-page"

#: A page with at most this many text runs is treated as visual (vector-dominant).
LOW_TEXT_RUN_THRESHOLD = 3

#: A vector figure overlapping an embedded raster by at least this fraction of
#: the figure's own area is considered the same region and dropped.
_RASTER_OVERLAP_DROP = 0.6

Bbox = tuple[float, float, float, float]


@dataclass(frozen=True)
class PlannedRegion:
    """One crop to feature-extract: its provenance, geometry, and PNG bytes."""

    page_number: int
    source: str
    bbox: Bbox | None
    xref: int | None
    png_bytes: bytes
    width_px: int
    height_px: int


def _area(bbox: Bbox) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _overlap_fraction(inner: Bbox, other: Bbox) -> float:
    """Fraction of ``inner``'s area covered by its intersection with ``other``."""
    ix0, iy0 = max(inner[0], other[0]), max(inner[1], other[1])
    ix1, iy1 = min(inner[2], other[2]), min(inner[3], other[3])
    intersection = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    inner_area = _area(inner)
    return intersection / inner_area if inner_area > 0 else 0.0


def plan_regions(
    embedded: list[ExtractedImage],
    page_text_runs: Mapping[int, int],
    render_full_page: Callable[[int], tuple[bytes, int, int]],
    vector_figures: Sequence[VectorFigure] = (),
    render_region: Callable[[int, Bbox], tuple[bytes, int, int]] | None = None,
) -> list[PlannedRegion]:
    """Plan the crops for a document.

    Args:
        embedded: Embedded raster placements from the image-source adapter.
        page_text_runs: ``page_number -> text_run_count`` (from ``document_pages``).
        render_full_page: ``page_number -> (png_bytes, width_px, height_px)``.
        vector_figures: Vector-drawing figure clusters from the image-source adapter.
        render_region: ``(page_number, bbox) -> (png_bytes, width_px, height_px)``;
            required when ``vector_figures`` is non-empty.
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
    rasters_by_page: dict[int, list[Bbox]] = {}
    for img in embedded:
        rasters_by_page.setdefault(img.page_number, []).append(img.bbox)

    full_page_pages: set[int] = set()
    for page_number, runs in sorted(page_text_runs.items()):
        if runs <= LOW_TEXT_RUN_THRESHOLD and page_number not in rasters_by_page:
            png, width, height = render_full_page(page_number)
            full_page_pages.add(page_number)
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

    for figure in vector_figures:
        # A page rendered whole already contains every vector figure on it.
        if figure.page_number in full_page_pages:
            continue
        # A figure that is really a placed raster is already captured as one.
        if any(
            _overlap_fraction(figure.bbox, raster) >= _RASTER_OVERLAP_DROP
            for raster in rasters_by_page.get(figure.page_number, ())
        ):
            continue
        if render_region is None:
            raise ValueError("render_region is required when vector_figures is non-empty")
        png, width, height = render_region(figure.page_number, figure.bbox)
        regions.append(
            PlannedRegion(
                page_number=figure.page_number,
                source=SOURCE_RENDERED_REGION,
                bbox=figure.bbox,
                xref=None,
                png_bytes=png,
                width_px=width,
                height_px=height,
            )
        )
    return regions
