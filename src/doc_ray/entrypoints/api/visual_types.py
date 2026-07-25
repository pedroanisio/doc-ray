"""GraphQL types for the Prism visual layer (Tier-1 image features)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

import strawberry
from strawberry.scalars import JSON


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


@strawberry.type
class CrossModalImageHit:
    """An image ranked by cross-modal (text->image) similarity."""

    id: strawberry.ID
    document_id: strawberry.ID
    page_number: int
    source: str
    object_key: str
    image_class: str | None
    similarity: float

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> CrossModalImageHit:
        """Map a ``cross_modal_image_search`` row."""
        return cls(
            id=strawberry.ID(str(row["id"])),
            document_id=strawberry.ID(str(row["document_id"])),
            page_number=int(row["page_number"]),
            source=str(row["source"]),
            object_key=str(row["object_key"]),
            image_class=row.get("image_class"),
            similarity=float(row["similarity"]),
        )


@strawberry.type
class VisualCaption:
    """A COMMITTED, grounded Tier-3 caption for a document image."""

    id: strawberry.ID
    document_id: strawberry.ID
    document_image_id: strawberry.ID
    page_number: int
    caption: str
    model: str
    grounding_score: float

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> VisualCaption:
        """Map a ``list_visual_captions`` row."""
        return cls(
            id=strawberry.ID(str(row["id"])),
            document_id=strawberry.ID(str(row["document_id"])),
            document_image_id=strawberry.ID(str(row["document_image_id"])),
            page_number=int(row["page_number"]),
            caption=str(row["caption"]),
            model=str(row["model"]),
            grounding_score=float(row["grounding_score"]),
        )


@strawberry.type
class VisualReviewItem:
    """An untrusted Tier-3 caption proposal awaiting an explicit decision."""

    id: strawberry.ID
    document_image_id: strawberry.ID
    document_id: strawberry.ID
    proposed: JSON
    grounding_score: float
    grounding_evidence: JSON
    status: str
    reviewer: strawberry.ID | None
    created_at: datetime
    updated_at: datetime
    page_number: int
    object_key: str
    cursor: str

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> VisualReviewItem:
        """Map a ``persistence_visual_read.list_visual_review`` row."""
        reviewer = row.get("reviewer")
        return cls(
            id=strawberry.ID(str(row["id"])),
            document_image_id=strawberry.ID(str(row["document_image_id"])),
            document_id=strawberry.ID(str(row["document_id"])),
            proposed=cast(JSON, row["proposed"]),
            grounding_score=float(row["grounding_score"]),
            grounding_evidence=cast(JSON, row.get("grounding_evidence") or {}),
            status=str(row["status"]),
            reviewer=strawberry.ID(str(reviewer)) if reviewer is not None else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            page_number=int(row["page_number"]),
            object_key=str(row["object_key"]),
            cursor=str(row["cursor"]),
        )
