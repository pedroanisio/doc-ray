"""Adapter: PyMuPDF implementation of the :class:`PdfImageSource` port.

Extracts embedded rasters via ``page.get_images`` + ``page.get_image_rects`` and
the native stream via ``doc.extract_image`` (preserving native resolution, per
proposal §5.3's EXIF/native-stream caveat), re-encoding to PNG for uniform crops.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any, cast

from PIL import Image

from doc_ray.ports.pdf_image_source import ExtractedImage, PdfImageSource


def _to_png(raw: bytes) -> tuple[bytes, int, int]:
    """Re-encode a native raster to PNG; return ``(png_bytes, width, height)``."""
    with Image.open(BytesIO(raw)) as img:
        rgb = img.convert("RGB")
    buffer = BytesIO()
    rgb.save(buffer, format="PNG")
    return buffer.getvalue(), rgb.width, rgb.height


class PyMuPDFImageSource(PdfImageSource):
    """Extract embedded raster images from a PDF via PyMuPDF."""

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
