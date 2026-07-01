"""Port: persistence of documents and their derived layers.

Fulfilled by ``doc_ray.adapters.persistence`` (Postgres via psycopg). The current
adapter exposes these as module-level functions taking a live connection; this
Protocol documents the contract the application relies on and is the seam at
which a different store could be substituted.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol


class Repository(Protocol):
    def find_by_sha256(self, conn: Any, sha256: str) -> Optional[dict]: ...

    def insert_document(self, conn: Any, **fields: Any) -> str:
        """Insert a document + its 4NF satellites (content, sentences, spans,
        authors, tags, provenance) in one transaction; return the new id."""
        ...

    def ensure_spans(self, conn: Any, document_id: str,
                     spans: list[tuple[int, int]]) -> dict[tuple[int, int], str]: ...

    def get_or_create_lexeme(self, conn: Any, cache: dict, lemma: str, pos: str,
                             lang: str) -> tuple[str, bool]: ...

    def replace_annotation(self, conn: Any, **run: Any) -> None: ...
