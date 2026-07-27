"""GraphQL reads for correction audit history and surface impact previews."""

from __future__ import annotations

import strawberry

from doc_ray.adapters import ner_corrections_store
from doc_ray.adapters import persistence_entity_surface_read as surface_read
from doc_ray.entrypoints.api.correction_types import (
    EntityMentionCorrection,
    EntitySurfaceOccurrence,
)
from doc_ray.entrypoints.api.ids import clean_uuid
from doc_ray.entrypoints.api.types import Info


def _explicit_document_ids(document_ids: list[strawberry.ID]) -> list[str]:
    """Validate, deduplicate, and bound an explicit surface-preview scope."""
    if not document_ids:
        raise ValueError("documentIds must contain at least one document")
    if len(document_ids) > surface_read.MAX_DOCUMENTS:
        raise ValueError(f"documentIds accepts at most {surface_read.MAX_DOCUMENTS} documents")
    cleaned: list[str] = []
    for value in document_ids:
        document_id = clean_uuid(str(value))
        if document_id is None:
            raise ValueError(f"invalid document id: {value}")
        cleaned.append(document_id)
    return list(dict.fromkeys(cleaned))


@strawberry.type
class CorrectionQueries:
    """Read surfaces for durable NER correction workflows."""

    @strawberry.field
    async def entity_mention_corrections(
        self, info: Info, document_id: strawberry.ID
    ) -> list[EntityMentionCorrection]:
        """Return a document's durable human NER corrections, oldest first."""
        doc_id = clean_uuid(str(document_id))
        if doc_id is None:
            return []
        rows = await ner_corrections_store.list_correction_rows_async(info.context.conn, doc_id)
        return [EntityMentionCorrection.from_row(row) for row in rows]

    @strawberry.field
    async def entity_surface_occurrences(
        self,
        info: Info,
        document_ids: list[strawberry.ID],
        surface: str,
        original_type: str,
        sample_limit: int = 5,
        count_cap: int = 1000,
    ) -> list[EntitySurfaceOccurrence]:
        """Preview an exact case-sensitive surface/current-type rule.

        Scope is always an explicit bounded list of documents. Each result makes
        its count cap visible and carries a few sentence-context samples; no
        corpus-global or fuzzy matching is performed.
        """
        ids = _explicit_document_ids(document_ids)
        rows = await surface_read.entity_surface_occurrences(
            info.context.conn,
            ids,
            surface=surface,
            original_type=original_type,
            sample_limit=sample_limit,
            count_cap=count_cap,
        )
        return [EntitySurfaceOccurrence.from_row(row) for row in rows]
