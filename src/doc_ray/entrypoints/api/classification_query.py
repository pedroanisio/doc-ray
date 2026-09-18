"""GraphQL read query for document genre classification (issue #6).

A one-field query group, split out of ``query.py`` as its own mixin (the
``StructureQueries`` / ``VisualQueries`` pattern) to keep that module under the
D-015 net-LOC cap. Mixed into the ``Query`` root in ``query.py``.
"""

from __future__ import annotations

import strawberry

from doc_ray.adapters import document_classification_store
from doc_ray.entrypoints.api.ids import clean_uuid as _clean_uuid
from doc_ray.entrypoints.api.types import DocumentClassification, Info


@strawberry.type
class ClassificationQueries:
    """Read a document's genre classification."""

    @strawberry.field
    async def document_classification(
        self, info: Info, document_id: strawberry.ID
    ) -> DocumentClassification | None:
        """Return a document's genre classification, or null if unclassified (issue #6).

        RLS scopes it to documents the caller may read (migration 127). A single PK
        lookup — O(1). A ``pending`` result is an unverified machine GUESS; only a
        ``confirmed`` row (``method='human'``) is a decided genre. Null for a
        malformed id or an unclassified document.
        """
        doc_id = _clean_uuid(str(document_id))
        if doc_id is None:
            return None
        row = await document_classification_store.get_classification_async(
            info.context.conn, doc_id
        )
        return DocumentClassification.from_row(row) if row else None
