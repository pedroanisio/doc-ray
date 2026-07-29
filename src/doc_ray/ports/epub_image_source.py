"""Port: extract embedded rasters from an EPUB container.

Separate from :mod:`doc_ray.ports.pdf_image_source` because the two formats do not
share a coordinate system. A PDF placement has a page and a bounding box in PDF
points; an EPUB is reflowable and has neither, so this port carries a spine index
(chapter position — a real property of the file) and no geometry at all, instead of
borrowing the PDF shape and filling the geometry with invented numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class EpubImage:
    """One embedded raster, located by spine position rather than page geometry."""

    spine_index: int  # 1-based index of the referencing content document
    archive_name: str  # zip entry name, for provenance and stable ordering
    png_bytes: bytes  # re-encoded as PNG so downstream featurizing is uniform
    width_px: int
    height_px: int


@runtime_checkable
class EpubImageSource(Protocol):
    """Detect embedded rasters in an EPUB given its raw bytes."""

    def embedded_images(self, epub_bytes: bytes) -> list[EpubImage]:
        """Return every decodable embedded raster, in spine then source order."""
        ...
