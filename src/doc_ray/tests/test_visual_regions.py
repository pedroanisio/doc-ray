"""Region planning: embedded rasters + vector figures + full-page renders."""

from __future__ import annotations

import pytest

from doc_ray.application.visual_regions import (
    SOURCE_EMBEDDED,
    SOURCE_FULL_PAGE,
    SOURCE_RENDERED_REGION,
    plan_regions,
)
from doc_ray.ports.pdf_image_source import ExtractedImage, VectorFigure


def _img(
    page: int, xref: int = 7, bbox: tuple[float, float, float, float] = (0.0, 0.0, 10.0, 10.0)
) -> ExtractedImage:
    return ExtractedImage(
        page_number=page,
        xref=xref,
        bbox=bbox,
        png_bytes=b"png",
        width_px=10,
        height_px=10,
    )


def _fig(
    page: int, bbox: tuple[float, float, float, float] = (100.0, 100.0, 200.0, 200.0)
) -> VectorFigure:
    return VectorFigure(page_number=page, bbox=bbox)


def _render(page: int) -> tuple[bytes, int, int]:
    return (f"page{page}".encode(), 100, 200)


def _render_region(page: int, bbox: tuple[float, float, float, float]) -> tuple[bytes, int, int]:
    return (f"region{page}@{bbox[0]:.0f},{bbox[1]:.0f}".encode(), 50, 60)


def test_embedded_rasters_are_always_regions() -> None:
    regions = plan_regions([_img(1), _img(2)], {1: 50, 2: 50}, _render)
    assert [r.source for r in regions] == [SOURCE_EMBEDDED, SOURCE_EMBEDDED]
    assert all(r.bbox is not None and r.xref == 7 for r in regions)


def test_low_text_page_without_raster_is_rendered_full_page() -> None:
    # page 1 is text-heavy (skip); page 2 is near-text-free (render whole).
    regions = plan_regions([], {1: 40, 2: 1}, _render)
    assert len(regions) == 1
    assert regions[0].source == SOURCE_FULL_PAGE
    assert regions[0].page_number == 2
    assert regions[0].png_bytes == b"page2" and regions[0].width_px == 100


def test_low_text_page_that_already_has_a_raster_is_not_double_counted() -> None:
    regions = plan_regions([_img(2)], {2: 0}, _render)
    assert len(regions) == 1
    assert regions[0].source == SOURCE_EMBEDDED  # no extra full-page render


def test_text_heavy_page_with_no_raster_yields_nothing() -> None:
    assert plan_regions([], {1: 200}, _render) == []


def test_vector_figure_on_text_heavy_page_is_a_rendered_region() -> None:
    # The core gap: a diagram drawn as vectors on a page full of prose. Neither
    # the raster extractor (no raster) nor the full-page fallback (page is not
    # near-text-free) sees it — the vector-figure source must.
    regions = plan_regions(
        [], {1: 80}, _render, [_fig(1, (100.0, 100.0, 213.0, 216.0))], _render_region
    )
    assert len(regions) == 1
    region = regions[0]
    assert region.source == SOURCE_RENDERED_REGION
    assert region.page_number == 1
    assert region.bbox == (100.0, 100.0, 213.0, 216.0)
    assert region.xref is None
    assert region.png_bytes == b"region1@100,100"
    assert (region.width_px, region.height_px) == (50, 60)


def test_vector_figure_overlapping_an_embedded_raster_is_dropped() -> None:
    # A "figure" that is really a placed raster is already captured as one; the
    # vector cluster over the same area must not double-count it.
    raster = _img(1, bbox=(100.0, 100.0, 200.0, 200.0))
    regions = plan_regions(
        [raster], {1: 80}, _render, [_fig(1, (105.0, 105.0, 195.0, 195.0))], _render_region
    )
    assert [r.source for r in regions] == [SOURCE_EMBEDDED]


def test_vector_figure_not_overlapping_a_raster_on_same_page_is_kept() -> None:
    raster = _img(1, bbox=(0.0, 0.0, 50.0, 50.0))
    regions = plan_regions(
        [raster], {1: 80}, _render, [_fig(1, (300.0, 300.0, 400.0, 400.0))], _render_region
    )
    assert [r.source for r in regions] == [SOURCE_EMBEDDED, SOURCE_RENDERED_REGION]


def test_vector_figure_on_full_page_rendered_page_is_dropped() -> None:
    # A near-text-free page is rendered whole; any vector figure on it is already
    # inside that full-page crop, so it must not be captured a second time.
    regions = plan_regions([], {1: 1}, _render, [_fig(1)], _render_region)
    assert [r.source for r in regions] == [SOURCE_FULL_PAGE]


def test_render_region_required_when_vector_figures_present() -> None:
    with pytest.raises(ValueError, match="render_region is required"):
        plan_regions([], {1: 80}, _render, [_fig(1)])
