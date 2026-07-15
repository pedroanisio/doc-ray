"""GraphQL types for the Prism visual layer (Tier-1 image features)."""

from __future__ import annotations

from typing import Any

import strawberry


@strawberry.type
class ColorSlice:
    """One dominant colour and its measured proportion of an image."""

    hex: str
    proportion: float


@strawberry.type
class DocumentImage:
    """A detected image/region and its deterministic Tier-1 visual features."""

    id: strawberry.ID
    document_id: strawberry.ID
    page_number: int
    source: str
    width_px: int
    height_px: int
    object_key: str
    sha256: str
    image_class: str | None
    mean_saturation: float | None
    mean_value: float | None
    contrast: float | None
    edge_density: float | None
    aspect_ratio: float | None
    dominant_colors: list[ColorSlice]
    cursor: str

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> DocumentImage:
        """Map a ``persistence_visual_read.list_document_images`` row."""
        palette = row.get("dominant_colors") or []
        return cls(
            id=strawberry.ID(str(row["id"])),
            document_id=strawberry.ID(str(row["document_id"])),
            page_number=int(row["page_number"]),
            source=str(row["source"]),
            width_px=int(row["width_px"]),
            height_px=int(row["height_px"]),
            object_key=str(row["object_key"]),
            sha256=str(row["sha256"]),
            image_class=row.get("image_class"),
            mean_saturation=row.get("mean_saturation"),
            mean_value=row.get("mean_value"),
            contrast=row.get("contrast"),
            edge_density=row.get("edge_density"),
            aspect_ratio=row.get("aspect_ratio"),
            dominant_colors=[
                ColorSlice(hex=str(c["hex"]), proportion=float(c["proportion"])) for c in palette
            ],
            cursor=str(row["cursor"]),
        )


@strawberry.type
class ImageClassCount:
    """Count of Tier-1 image regions of one class in a document."""

    image_class: str
    count: int

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ImageClassCount:
        """Map an ``image_class_summary`` row."""
        return cls(image_class=str(row["image_class"]), count=int(row["count"]))
