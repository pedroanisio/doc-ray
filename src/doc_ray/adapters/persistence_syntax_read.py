"""Async read adapter for the derived syntax layers (9 · constituency, 14 · SRL).

Separate from :mod:`doc_ray.adapters.persistence_layers_read` because both layers
are *derived* projections of the dependency parse rather than direct spaCy output,
and their read shapes are trees, not flat span lists.

Neither ``constituent`` nor ``srl_predicate`` stores a surface string. It is cut
from ``document_content.full_text`` at read time instead, so a node can never
disagree with the text it claims to cover -- the failure mode a denormalized copy
invites once the document is re-extracted.
"""

from __future__ import annotations

from typing import Any

import psycopg

from doc_ray.adapters.read_cursors import decode_cursor, encode_cursor

Row = dict[str, Any]
_AConn = psycopg.AsyncConnection[Row]


async def _rows(conn: _AConn, sql: str, params: dict[str, Any]) -> list[Row]:
    cur = await conn.execute(sql, params)
    return await cur.fetchall()


def _surface_sql(alias: str) -> str:
    return (
        f"substring(c.full_text FROM {alias}.char_start + 1 "
        f"FOR {alias}.char_end - {alias}.char_start)"
    )


async def list_constituents(
    conn: _AConn, document_id: str, *, limit: int = 200, after: str | None = None
) -> list[Row]:
    """Return phrase-structure nodes for one document, outermost first.

    The ordering is what makes the tree rebuildable by streaming: a parent's span
    always starts no later than its children's and ends no earlier, so sorting by
    ``char_start`` ascending and ``char_end`` descending emits every parent before
    the nodes that reference it.
    """
    cursor = decode_cursor(after, "constituents")
    rows = await _rows(
        conn,
        f"""
        SELECT cn.id, a.document_id, cn.annotation_id, cn.parent_id, cn.label,
               sp.char_start, sp.char_end, {_surface_sql("sp")} AS surface
        FROM constituent cn
        JOIN annotations a ON a.id = cn.annotation_id
        JOIN spans sp ON sp.id = cn.span_id
        JOIN document_content c ON c.document_id = a.document_id
        WHERE a.document_id = %(doc)s::uuid
          AND (%(after_start)s::int IS NULL
               OR sp.char_start > %(after_start)s::int
               OR (sp.char_start = %(after_start)s::int
                   AND sp.char_end < %(after_end)s::int)
               OR (sp.char_start = %(after_start)s::int
                   AND sp.char_end = %(after_end)s::int
                   AND cn.id > %(after_id)s::ulid))
        ORDER BY sp.char_start, sp.char_end DESC, cn.id
        LIMIT %(limit)s
        """,
        {
            "doc": document_id,
            "limit": limit,
            "after_start": cursor["char_start"] if cursor else None,
            "after_end": cursor["char_end"] if cursor else None,
            "after_id": cursor["id"] if cursor else None,
        },
    )
    for row in rows:
        row["cursor"] = encode_cursor(
            "constituents",
            char_start=row["char_start"],
            char_end=row["char_end"],
            id=row["id"],
        )
    return rows


async def list_srl_frames(
    conn: _AConn, document_id: str, *, limit: int = 100, after: str | None = None
) -> list[Row]:
    """Return predicate-argument frames for one document, in document order.

    ``frame`` is the predicate lemma, stored that way on purpose: a PropBank
    roleset index (``found.01``) encodes a sense distinction this derivation never
    made. Arguments come back inline -- a frame with its roles split across a
    second round-trip is not a frame anyone can read.
    """
    cursor = decode_cursor(after, "srl-frames")
    rows = await _rows(
        conn,
        f"""
        SELECT p.id, a.document_id, p.annotation_id, p.frame,
               sp.char_start, sp.char_end, {_surface_sql("sp")} AS surface,
               COALESCE((
                   SELECT jsonb_agg(
                              jsonb_build_object(
                                  'role', arg.role,
                                  'char_start', asp.char_start,
                                  'char_end', asp.char_end,
                                  'surface', {_surface_sql("asp")}
                              )
                              ORDER BY arg.role, asp.char_start
                          )
                   FROM srl_argument arg
                   JOIN spans asp ON asp.id = arg.span_id
                   WHERE arg.predicate_id = p.id
               ), '[]'::jsonb) AS arguments
        FROM srl_predicate p
        JOIN annotations a ON a.id = p.annotation_id
        JOIN tokens t ON t.id = p.token_id
        JOIN spans sp ON sp.id = t.span_id
        JOIN document_content c ON c.document_id = a.document_id
        WHERE a.document_id = %(doc)s::uuid
          AND (%(after_start)s::int IS NULL
               OR sp.char_start > %(after_start)s::int
               OR (sp.char_start = %(after_start)s::int AND p.id > %(after_id)s::ulid))
        ORDER BY sp.char_start, p.id
        LIMIT %(limit)s
        """,
        {
            "doc": document_id,
            "limit": limit,
            "after_start": cursor["char_start"] if cursor else None,
            "after_id": cursor["id"] if cursor else None,
        },
    )
    for row in rows:
        row["cursor"] = encode_cursor(
            "srl-frames", char_start=row["char_start"], id=row["id"]
        )
    return rows
