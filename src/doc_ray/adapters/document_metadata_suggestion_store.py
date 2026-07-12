"""Persistence for verified document-metadata suggestions (BL-061)."""

from __future__ import annotations

from typing import Any

import psycopg

from doc_ray.domain.ulid import new_ulid_str

Row = dict[str, Any]
_Conn = psycopg.Connection[Row]
_AConn = psycopg.AsyncConnection[Row]

_LIST_SQL = (
    "SELECT id, document_id, field, current_value, suggested_value, confidence, "
    "       reason, source, status, created_at, decided_at "
    "FROM document_metadata_suggestion "
    "WHERE document_id = %s AND (%s::text IS NULL OR status = %s) "
    "ORDER BY status, confidence DESC, field, suggested_value"
)


def upsert_suggestion(
    conn: _Conn,
    *,
    document_id: str,
    field: str,
    current_value: str | None,
    suggested_value: str,
    confidence: float,
    reason: str,
    source: str = "verified-deterministic",
) -> None:
    """Insert or refresh a pending suggestion; decided rows keep their decision."""
    conn.execute(
        "INSERT INTO document_metadata_suggestion "
        "  (id, document_id, field, current_value, suggested_value, confidence, reason, source) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (document_id, field, suggested_value) DO UPDATE SET "
        "  current_value = EXCLUDED.current_value, confidence = EXCLUDED.confidence, "
        "  reason = EXCLUDED.reason, source = EXCLUDED.source "
        "WHERE document_metadata_suggestion.status = 'pending'",
        (
            new_ulid_str(),
            document_id,
            field,
            current_value,
            suggested_value,
            confidence,
            reason,
            source,
        ),
    )


def list_for_document(conn: _Conn, document_id: str, status: str | None = None) -> list[Row]:
    """Return one document's metadata suggestions."""
    return conn.execute(_LIST_SQL, (document_id, status, status)).fetchall()


async def list_for_document_async(
    conn: _AConn, document_id: str, status: str | None = None
) -> list[Row]:
    """Async :func:`list_for_document` for GraphQL."""
    cur = await conn.execute(_LIST_SQL, (document_id, status, status))
    return await cur.fetchall()


def decide(conn: _Conn, suggestion_ids: list[str], status: str) -> int:
    """Approve or reject suggestions; returns rows updated."""
    cur = conn.execute(
        "UPDATE document_metadata_suggestion SET status = %s, decided_at = now() "
        "WHERE id = ANY(%s) AND status = 'pending'",
        (status, suggestion_ids),
    )
    return cur.rowcount


async def decide_async(conn: _AConn, suggestion_ids: list[str], status: str) -> int:
    """Async :func:`decide` for GraphQL."""
    cur = await conn.execute(
        "UPDATE document_metadata_suggestion SET status = %s, decided_at = now() "
        "WHERE id = ANY(%s) AND status = 'pending'",
        (status, suggestion_ids),
    )
    return cur.rowcount
