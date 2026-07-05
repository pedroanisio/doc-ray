"""Async read adapter for canonicalization and review surfaces."""

from __future__ import annotations

from typing import Any

import psycopg

from doc_ray.adapters.read_cursors import decode_cursor, encode_cursor

Row = dict[str, Any]
_AConn = psycopg.AsyncConnection[Row]


async def _rows(conn: _AConn, sql: str, params: dict[str, Any]) -> list[Row]:
    cur = await conn.execute(sql, params)
    return await cur.fetchall()


async def _row(conn: _AConn, sql: str, params: dict[str, Any]) -> Row | None:
    cur = await conn.execute(sql, params)
    return await cur.fetchone()


async def _forms(conn: _AConn, canonical_ids: list[int]) -> dict[int, list[Row]]:
    rows = await _rows(
        conn,
        """
        SELECT canonical_id, form_norm, form_surface, lang, form_type, confidence,
               occurrences, source_types
        FROM form_index
        WHERE canonical_id = ANY(%(ids)s::bigint[])
        ORDER BY canonical_id, form_type, form_surface
        """,
        {"ids": canonical_ids},
    )
    out: dict[int, list[Row]] = {i: [] for i in canonical_ids}
    for row in rows:
        out[int(row["canonical_id"])].append(row)
    return out


async def get_canonical(conn: _AConn, canonical_id: int) -> Row | None:
    """Return one canonical entity with its registered forms."""
    row = await _row(conn, "SELECT * FROM canonical WHERE id = %(id)s", {"id": canonical_id})
    if row is None:
        return None
    row["forms"] = (await _forms(conn, [canonical_id]))[canonical_id]
    row["cursor"] = encode_cursor("canonical", id=row["id"])
    return row


async def search_canonical(
    conn: _AConn, q: str, *, limit: int = 50, after: str | None = None
) -> list[Row]:
    """Search canonical registry rows by preferred label, Wikidata id, or form."""
    cursor = decode_cursor(after, "canonical-search")
    rows = await _rows(
        conn,
        """
        SELECT DISTINCT c.*
        FROM canonical c
        LEFT JOIN form_index f ON f.canonical_id = c.id
        WHERE (%(q)s = '' OR c.preferred ILIKE %(pat)s OR c.wikidata_id ILIKE %(pat)s
               OR f.form_surface ILIKE %(pat)s)
          AND (%(after_pref)s::text IS NULL OR c.preferred > %(after_pref)s
               OR (c.preferred = %(after_pref)s AND c.id > %(after_id)s::bigint))
        ORDER BY c.preferred, c.id
        LIMIT %(limit)s
        """,
        {
            "q": q,
            "pat": f"%{q}%",
            "limit": limit,
            "after_pref": cursor["preferred"] if cursor else None,
            "after_id": cursor["id"] if cursor else None,
        },
    )
    forms = await _forms(conn, [int(row["id"]) for row in rows])
    for row in rows:
        row["forms"] = forms[int(row["id"])]
        row["cursor"] = encode_cursor("canonical-search", preferred=row["preferred"], id=row["id"])
    return rows


async def list_unresolved_mentions(
    conn: _AConn, document_id: str, *, limit: int = 100, after: str | None = None
) -> list[Row]:
    """Return entity mentions in a document without a canonical link."""
    cursor = decode_cursor(after, "unresolved-mentions")
    rows = await _rows(
        conn,
        """
        SELECT em.id AS mention_id, em.entity_type, sp.char_start, sp.char_end,
               substring(c.full_text FROM sp.char_start + 1
                         FOR sp.char_end - sp.char_start) AS surface
        FROM entity_mention em
        JOIN annotations a ON a.id = em.annotation_id
        JOIN spans sp ON sp.id = em.span_id
        JOIN document_content c ON c.document_id = a.document_id
        LEFT JOIN entity_link el ON el.mention_id = em.id AND el.scheme = 'canonical'
        WHERE a.document_id = %(doc)s::uuid AND el.mention_id IS NULL
          AND (%(after_start)s::int IS NULL OR sp.char_start > %(after_start)s::int
               OR (sp.char_start = %(after_start)s::int AND em.id > %(after_id)s::ulid))
        ORDER BY sp.char_start, em.id
        LIMIT %(limit)s
        """,
        {
            "doc": document_id,
            "limit": limit,
            "after_start": cursor["char_start"] if cursor else None,
            "after_id": cursor["mention_id"] if cursor else None,
        },
    )
    for row in rows:
        row["cursor"] = encode_cursor(
            "unresolved-mentions", char_start=row["char_start"], mention_id=row["mention_id"]
        )
    return rows


async def list_resolution_review(
    conn: _AConn,
    *,
    document_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
    after: str | None = None,
) -> list[Row]:
    """Return review rows, optionally scoped by document and status."""
    cursor = decode_cursor(after, "resolution-review")
    rows = await _rows(
        conn,
        """
        SELECT rr.id, rr.mention_id, rr.candidates, rr.proposed_canonical_id,
               rr.confidence, rr.status, rr.reason, rr.created_at, rr.decided_at,
               c.preferred AS proposed_preferred, c.entity_type AS proposed_entity_type,
               c.wikidata_id AS proposed_wikidata_id
        FROM resolution_review rr
        JOIN entity_mention em ON em.id = rr.mention_id
        JOIN annotations a ON a.id = em.annotation_id
        LEFT JOIN canonical c ON c.id = rr.proposed_canonical_id
        WHERE (%(doc)s::uuid IS NULL OR a.document_id = %(doc)s::uuid)
          AND (%(status)s = '' OR rr.status = %(status)s)
          AND (%(after_created)s::timestamptz IS NULL
               OR rr.created_at < %(after_created)s::timestamptz
               OR (rr.created_at = %(after_created)s::timestamptz AND rr.id > %(after_id)s::ulid))
        ORDER BY rr.created_at DESC, rr.id
        LIMIT %(limit)s
        """,
        {
            "doc": document_id,
            "status": status or "",
            "limit": limit,
            "after_created": cursor["created_at"] if cursor else None,
            "after_id": cursor["id"] if cursor else None,
        },
    )
    for row in rows:
        row["cursor"] = encode_cursor(
            "resolution-review", created_at=row["created_at"], id=row["id"]
        )
    return rows
