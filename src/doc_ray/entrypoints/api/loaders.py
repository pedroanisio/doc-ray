"""Per-request DataLoaders that batch by-document reads to defeat N+1.

Listing documents and selecting each one's authors/tags/metadata/sentence-count
would otherwise fire one query per document. Each loader batches those keys into a
single ``WHERE document_id = ANY(...)`` round-trip via the read adapter. Loaders are
constructed fresh per request (so their identity-map cache never crosses requests)
and share the request's single pooled connection.
"""

from __future__ import annotations

from typing import Any

import psycopg
from strawberry.dataloader import DataLoader

from doc_ray.adapters import persistence_read as read


class Loaders:
    """A fresh set of by-document DataLoaders bound to one connection."""

    def __init__(self, conn: psycopg.AsyncConnection[dict[str, Any]]) -> None:
        """Build the loaders over *conn* (the request's pooled connection)."""
        self._conn = conn
        self.authors: DataLoader[str, list[str]] = DataLoader(load_fn=self._authors)
        self.tags: DataLoader[str, list[str]] = DataLoader(load_fn=self._tags)
        self.metadata: DataLoader[str, dict[str, str]] = DataLoader(load_fn=self._metadata)
        self.sentence_count: DataLoader[str, int] = DataLoader(load_fn=self._sentence_count)

    async def _authors(self, keys: list[str]) -> list[list[str]]:
        by_doc = await read.authors_by_documents(self._conn, list(keys))
        return [by_doc.get(str(k), []) for k in keys]

    async def _tags(self, keys: list[str]) -> list[list[str]]:
        by_doc = await read.tags_by_documents(self._conn, list(keys))
        return [by_doc.get(str(k), []) for k in keys]

    async def _metadata(self, keys: list[str]) -> list[dict[str, str]]:
        by_doc = await read.metadata_by_documents(self._conn, list(keys))
        return [by_doc.get(str(k), {}) for k in keys]

    async def _sentence_count(self, keys: list[str]) -> list[int]:
        by_doc = await read.sentence_counts_by_documents(self._conn, list(keys))
        return [by_doc.get(str(k), 0) for k in keys]
