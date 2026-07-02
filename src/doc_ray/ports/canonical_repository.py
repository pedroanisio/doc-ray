"""Port: persistence for the canonical registry and the remissive index.

Fulfilled by :mod:`doc_ray.adapters.canonical_store`. Like the other repositories,
methods take a live connection they do not own (autocommit; the caller manages it).
"""

from __future__ import annotations

from typing import Any, Protocol

import psycopg

Row = dict[str, Any]


class CanonicalRepository(Protocol):
    """Read/write canonical entities and the form -> canonical remissive index."""

    def create_canonical(self, conn: psycopg.Connection[Any], **fields: object) -> int:
        """Insert a canonical entity and return its new id."""
        ...

    def lookup_form(self, conn: psycopg.Connection[Any], form_norm: str) -> list[Row]:
        """Return remissive-index rows whose normalized form equals *form_norm*."""
        ...

    def upsert_form(self, conn: psycopg.Connection[Any], **row: object) -> None:
        """Insert or reinforce one ``form_index`` row (the flywheel write-back)."""
        ...

    def forms_for(self, conn: psycopg.Connection[Any], canonical_id: int) -> list[Row]:
        """Return every surface form registered for a canonical entity."""
        ...
