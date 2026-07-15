"""Region planning: embedded rasters + full-page renders of near-text-free pages."""

from __future__ import annotations

from doc_ray.application.visual_regions import (
    SOURCE_EMBEDDED,
    SOURCE_FULL_PAGE,
    plan_regions,
)
from doc_ray.ports.pdf_image_source import ExtractedImage


def _img(page: int, xref: int = 7) -> ExtractedImage:
    return ExtractedImage(
        page_number=page,
        xref=xref,
        bbox=(0.0, 0.0, 10.0, 10.0),
        png_bytes=b"png",
        width_px=10,
        height_px=10,
    )


def _render(page: int) -> tuple[bytes, int, int]:
    return (f"page{page}".encode(), 100, 200)


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
