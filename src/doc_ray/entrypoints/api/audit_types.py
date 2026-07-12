"""GraphQL types for document-metadata edit audit trails (BL-058).

Split out of ``types.py`` (the project already splits GraphQL types by
concern — ``canonical_types.py``, ``layer_types.py``) to stay within the
D-015 400-net-LOC cap.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import strawberry

Row = dict[str, Any]


@strawberry.type
class DocumentMetadataChange:
    """One edit to a document's title/description/tags (BL-058 audit trail).

    Distinct from ``document_provenance`` (pipeline/ingest events, exposed
    separately) — this is the record of direct edits to a document's own
    descriptive metadata via ``updateDocumentMetadata``.
    """

    id: strawberry.ID
    document_id: strawberry.ID
    field: str
    old_value: str | None
    new_value: str | None
    changed_at: datetime
    actor: str | None

    @classmethod
    def from_row(cls, row: Row) -> DocumentMetadataChange:
        """Build a ``DocumentMetadataChange`` from a history-table row."""
        return cls(
            id=strawberry.ID(str(row["id"])),
            document_id=strawberry.ID(str(row["document_id"])),
            field=row["field"],
            old_value=row.get("old_value"),
            new_value=row.get("new_value"),
            changed_at=row["changed_at"],
            actor=row.get("actor"),
        )


@strawberry.type
class DocumentMetadataSuggestion:
    """One verified title/tag/duplicate suggestion awaiting human review."""

    id: strawberry.ID
    document_id: strawberry.ID
    field: str
    current_value: str | None
    suggested_value: str
    confidence: float
    reason: str
    source: str
    status: str
    created_at: datetime
    decided_at: datetime | None

    @strawberry.field
    def confidence_band(self) -> str:
        """Return the visible confidence bucket for review UI."""
        if self.confidence >= 0.85:
            return "high"
        if self.confidence >= 0.75:
            return "medium"
        return "low"

    @classmethod
    def from_row(cls, row: Row) -> DocumentMetadataSuggestion:
        """Build a ``DocumentMetadataSuggestion`` from a suggestion row."""
        return cls(
            id=strawberry.ID(str(row["id"])),
            document_id=strawberry.ID(str(row["document_id"])),
            field=row["field"],
            current_value=row.get("current_value"),
            suggested_value=row["suggested_value"],
            confidence=float(row["confidence"]),
            reason=row["reason"],
            source=row["source"],
            status=row["status"],
            created_at=row["created_at"],
            decided_at=row.get("decided_at"),
        )
