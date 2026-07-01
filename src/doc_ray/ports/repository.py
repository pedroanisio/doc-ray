"""Port: persistence of documents and their derived layers.

Fulfilled by ``doc_ray.adapters.persistence`` (Postgres via psycopg). The current
adapter exposes these as module-level functions taking a live connection; this
Protocol documents the contract the application relies on and is the seam at
which a different store could be substituted.
"""

from __future__ import annotations

from typing import Any, Protocol

import psycopg


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
