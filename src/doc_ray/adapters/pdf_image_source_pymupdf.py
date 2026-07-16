"""Adapter: PyMuPDF implementation of the :class:`PdfImageSource` port.

Extracts embedded rasters via ``page.get_images`` + ``page.get_image_rects`` and
the native stream via ``doc.extract_image`` (preserving native resolution, per
proposal §5.3's EXIF/native-stream caveat), re-encoding to PNG for uniform crops.
Vector-graphics figures are detected via ``page.cluster_drawings`` (proposal
§5.3, Stage 1) and returned as geometry only — the renderer rasterizes them.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any, cast

from PIL import Image

from doc_ray.ports.pdf_image_source import ExtractedImage, PdfImageSource, VectorFigure

# --- Vector-figure cluster filter (calibrated on the corpus) ----------------
# ``cluster_drawings`` groups vector paths into candidate figures but also
# returns page-frame rectangles and hairline rules. Keep only clusters large
# enough to be a real diagram and reject frames/underlines. Thresholds were
# calibrated against production fixtures (music-theory diagrams read at 4-7% of
# the page; page borders at >92%; rules exceed the aspect cap).
#: Minimum cluster area as a fraction of page area to count as a figure.
MIN_FIGURE_AREA_FRAC = 0.03
#: Maximum area fraction — a near-full-page cluster on a text page is a page
#: frame, not a figure (a genuinely full-page vector diagram is a near-text-free
#: page, already captured by the full-page-render fallback).
MAX_FIGURE_AREA_FRAC = 0.92
#: Minimum length of each side, in PDF points (~0.55 inch), to reject specks.
MIN_FIGURE_SIDE_PT = 40.0
#: Maximum side-length ratio; rules/underlines are far thinner than any figure.
MAX_FIGURE_ASPECT = 12.0


def _to_png(raw: bytes) -> tuple[bytes, int, int]:
    """Re-encode a native raster to PNG; return ``(png_bytes, width, height)``."""
    with Image.open(BytesIO(raw)) as img:
        rgb = img.convert("RGB")
    buffer = BytesIO()
    rgb.save(buffer, format="PNG")
    return buffer.getvalue(), rgb.width, rgb.height


class PyMuPDFImageSource(PdfImageSource):
    """Detect embedded rasters and vector-figure regions in a PDF via PyMuPDF."""

    def vector_figures(self, pdf_bytes: bytes) -> list[VectorFigure]:
        """Return figure-sized vector-drawing clusters, in page then reading order."""
        import fitz

        out: list[VectorFigure] = []
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            for page_index in range(doc.page_count):
                page = doc.load_page(page_index)
                page_area = float(page.rect.width) * float(page.rect.height)
                if page_area <= 0:
                    continue
                min_area = MIN_FIGURE_AREA_FRAC * page_area
                max_area = MAX_FIGURE_AREA_FRAC * page_area
                for cluster in page.cluster_drawings():
                    width, height = float(cluster.width), float(cluster.height)
                    area = width * height
                    if not min_area <= area <= max_area:
                        continue
                    if width < MIN_FIGURE_SIDE_PT or height < MIN_FIGURE_SIDE_PT:
                        continue
                    if max(width / height, height / width) > MAX_FIGURE_ASPECT:
                        continue
                    out.append(
                        VectorFigure(
                            page_number=page_index + 1,
                            bbox=(
                                float(cluster.x0),
                                float(cluster.y0),
                                float(cluster.x1),
                                float(cluster.y1),
                            ),
                        )
                    )
        return out

    def embedded_images(self, pdf_bytes: bytes) -> list[ExtractedImage]:
        """Return every embedded raster placement, in page then reading order."""
        import fitz

        out: list[ExtractedImage] = []
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            for page_index in range(doc.page_count):
                page = doc.load_page(page_index)
                seen_xrefs = {img[0] for img in page.get_images(full=True)}
                for xref in sorted(seen_xrefs):
                    rects = page.get_image_rects(xref)
                    if not rects:
                        continue
                    try:
                        extracted = cast(dict[str, Any], doc.extract_image(xref))
                        png, width, height = _to_png(extracted["image"])
                    except (KeyError, OSError, ValueError):
                        continue  # unsupported/corrupt stream — skip, don't fail the doc
                    for rect in rects:
                        out.append(
                            ExtractedImage(
                                page_number=page_index + 1,
                                xref=int(xref),
                                bbox=(
                                    float(rect.x0),
                                    float(rect.y0),
                                    float(rect.x1),
                                    float(rect.y1),
                                ),
                                png_bytes=png,
                                width_px=width,
                                height_px=height,
                            )
                        )
        return out
