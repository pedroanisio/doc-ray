"""Persistence for seed suggestions — the curatable domain-seeding staging area.

Module-level functions over a live autocommit ``dict_row`` connection (sync, for
the worker and webapp reads) plus async variants for the GraphQL API. Rows move
``pending -> approved | rejected -> seeded``; ``seeded`` is final — a curator can
flip a pending/approved/rejected decision, but history of what entered the
registry is never rewritten.
"""

from __future__ import annotations

from typing import Any

import psycopg

from doc_ray.domain.ulid import new_ulid_str

Row = dict[str, Any]
_Conn = psycopg.Connection[Row]
_AConn = psycopg.AsyncConnection[Row]

_LIST_SQL = (
    "SELECT id, document_id, term, occurrences, source, qid, label, description, "
    "       entity_type, status, created_at, decided_at "
    "FROM seed_suggestion WHERE document_id = %s AND (%s::text IS NULL OR status = %s) "
    "ORDER BY (qid IS NULL), occurrences DESC, term"
)

_DECIDE_SQL = (
    "UPDATE seed_suggestion SET status = %s, decided_at = now() "
    "WHERE id = ANY(%s) AND status <> 'seeded'"
)


def upsert_suggestion(
    conn: _Conn,
    *,
    document_id: str,
    term: str,
    occurrences: int,
    source: str,
    qid: str | None,
    label: str | None,
    description: str | None,
    entity_type: str | None,
) -> None:
    """Insert or refresh one suggestion; a decided row keeps its decision.

    Re-mining a document must update resolution details of still-pending rows
    (counts drift, the KB improves) without ever reverting a curator's call.
    """
    conn.execute(
        "INSERT INTO seed_suggestion "
        "  (id, document_id, term, occurrences, source, qid, label, description, entity_type) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (document_id, term) DO UPDATE SET "
        "  occurrences = EXCLUDED.occurrences, source = EXCLUDED.source, "
        "  qid = EXCLUDED.qid, label = EXCLUDED.label, "
        "  description = EXCLUDED.description, entity_type = EXCLUDED.entity_type "
        "WHERE seed_suggestion.status = 'pending'",
        (
            new_ulid_str(),
            document_id,
            term,
            occurrences,
            source,
            qid,
            label,
            description,
            entity_type,
        ),
    )


def list_for_document(conn: _Conn, document_id: str, status: str | None = None) -> list[Row]:
    """Return a document's suggestions (unresolved last, then by frequency)."""
    return conn.execute(_LIST_SQL, (document_id, status, status)).fetchall()


def decide(conn: _Conn, suggestion_ids: list[str], status: str) -> int:
    """Set *status* on the given suggestions; ``seeded`` rows are immutable.

    Returns:
        The number of rows actually updated.
    """
    cur = conn.execute(_DECIDE_SQL, (status, suggestion_ids))
    return cur.rowcount


def approved_qids(conn: _Conn, document_id: str) -> list[str]:
    """Return the distinct QIDs of this document's approved, resolved suggestions."""
    rows = conn.execute(
        "SELECT DISTINCT qid FROM seed_suggestion "
        "WHERE document_id = %s AND status = 'approved' AND qid IS NOT NULL ORDER BY qid",
        (document_id,),
    ).fetchall()
    return [str(r["qid"]) for r in rows]


def mark_seeded(conn: _Conn, document_id: str) -> int:
    """Advance this document's approved+resolved suggestions to ``seeded``."""
    cur = conn.execute(
        "UPDATE seed_suggestion SET status = 'seeded', decided_at = now() "
        "WHERE document_id = %s AND status = 'approved' AND qid IS NOT NULL",
        (document_id,),
    )
    return cur.rowcount


async def list_for_document_async(
    conn: _AConn, document_id: str, status: str | None = None
) -> list[Row]:
    """Async :func:`list_for_document` for the GraphQL API."""
    cur = await conn.execute(_LIST_SQL, (document_id, status, status))
    return await cur.fetchall()


async def decide_async(conn: _AConn, suggestion_ids: list[str], status: str) -> int:
    """Async :func:`decide` for the GraphQL API."""
    cur = await conn.execute(_DECIDE_SQL, (status, suggestion_ids))
    return cur.rowcount
