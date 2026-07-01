"""Port: persistence of documents and their derived layers.

Fulfilled by ``doc_ray.adapters.persistence`` (Postgres via psycopg). The current
adapter exposes these as module-level functions taking a live connection; this
Protocol documents the contract the application relies on and is the seam at
which a different store could be substituted.
"""

from __future__ import annotations

from typing import Any, Protocol

import psycopg

#: Rows are returned as plain dicts (``row_factory=dict_row``); the API layer maps
#: them to GraphQL types. Aliased for the async read surface below.
Row = dict[str, Any]


class Repository(Protocol):
    """Persist documents and their derived (content/span/lexeme) layers."""

    def find_by_sha256(self, conn: psycopg.Connection[Any], sha256: str) -> dict[str, Any] | None:
        """Return the stored document row matching *sha256*, or ``None``.

        Args:
            conn: Live database connection.
            sha256: Hex content hash of the original document bytes.

        Returns:
            The matching document row, or ``None`` when nothing is stored.
        """
        ...

    def insert_document(self, conn: psycopg.Connection[Any], **fields: object) -> str:
        """Insert a document and its satellites in one transaction.

        Persists the document plus its 4NF satellites (content, sentences,
        spans, authors, tags, provenance).

        Args:
            conn: Live database connection.
            **fields: Column values for the document and its satellites.

        Returns:
            The id of the newly inserted document.
        """
        ...

    def ensure_spans(
        self,
        conn: psycopg.Connection[Any],
        document_id: str,
        spans: list[tuple[int, int]],
    ) -> dict[tuple[int, int], str]:
        """Register ``(start, end)`` spans (idempotently) and map them to ids.

        Args:
            conn: Live database connection.
            document_id: Owning document id.
            spans: ``(char_start, char_end)`` offset pairs.

        Returns:
            A ``{(start, end): span_id}`` map for every requested span.
        """
        ...

    def get_or_create_lexeme(
        self,
        conn: psycopg.Connection[Any],
        cache: dict[Any, Any],
        lemma: str,
        pos: str,
        lang: str,
    ) -> tuple[str, bool]:
        """Resolve ``(lemma, pos, lang)`` to a lexeme uid, minting if new.

        Args:
            conn: Live database connection.
            cache: Per-run memo mapping keys to uids.
            lemma: Casefolded lemma.
            pos: Universal part-of-speech tag.
            lang: Language code.

        Returns:
            A ``(uid, created)`` pair; ``created`` is ``True`` when minted.
        """
        ...

    def replace_annotation(self, conn: psycopg.Connection[Any], **run: object) -> None:
        """Atomically (re)write one annotation run for a document.

        Args:
            conn: Live database connection.
            **run: Fields describing the annotation run (occurrences,
                provenance, layer, tool, and so on).
        """
        ...


class ReadRepository(Protocol):
    """Async, read-only query surface shared by the API, browser, and export.

    Fulfilled by :mod:`doc_ray.adapters.persistence_read`. Every method takes an
    ``AsyncConnection`` (``row_factory=dict_row``) and returns plain dict rows or
    ``None``; the connection is owned by the caller (a pool in the API layer), so
    the repository never opens, closes, or commits. This is the single source of
    truth for reads, collapsing the SQL previously duplicated across the Flask
    browser and the export CLI.
    """

    async def get_document(
        self, conn: psycopg.AsyncConnection[Row], document_id: str
    ) -> Row | None:
        """Return one document with its 4NF satellites, or ``None`` if absent."""
        ...

    async def list_documents(
        self, conn: psycopg.AsyncConnection[Row], *, limit: int = 50, offset: int = 0
    ) -> list[Row]:
        """Return document summary rows (with counts) newest-first."""
        ...

    async def list_sentences(
        self,
        conn: psycopg.AsyncConnection[Row],
        document_id: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Row]:
        """Return a document's sentences (with span offsets) in ordinal order."""
        ...

    async def get_provenance(
        self, conn: psycopg.AsyncConnection[Row], document_id: str
    ) -> list[Row]:
        """Return a document's provenance events (params reassembled) in order."""
        ...

    async def get_lexeme(self, conn: psycopg.AsyncConnection[Row], uid: str) -> Row | None:
        """Return one lexeme by uid, or ``None`` if absent."""
        ...

    async def list_lexemes(
        self,
        conn: psycopg.AsyncConnection[Row],
        *,
        q: str | None = None,
        pos: str | None = None,
        lang: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Row]:
        """Return lexemes filtered by lemma substring / pos / lang, busiest-first."""
        ...

    async def lexeme_kwic(
        self, conn: psycopg.AsyncConnection[Row], uid: str, *, limit: int = 80
    ) -> list[Row]:
        """Return keyword-in-context occurrences (pre/hit/post) for a lexeme."""
        ...

    async def search_sentences(
        self, conn: psycopg.AsyncConnection[Row], term: str, *, limit: int = 100
    ) -> list[Row]:
        """Return sentences containing *term* (case-insensitive substring)."""
        ...

    async def fulltext_search(
        self, conn: psycopg.AsyncConnection[Row], query: str, *, limit: int = 25
    ) -> list[Row]:
        """Return documents ranked by full-text match against ``document_content.fts``."""
        ...
