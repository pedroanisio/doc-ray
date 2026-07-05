"""Async read adapter for persisted NLP annotation layers."""

from __future__ import annotations

from typing import Any

import psycopg

from doc_ray.adapters.read_cursors import decode_cursor, encode_cursor

Row = dict[str, Any]
_AConn = psycopg.AsyncConnection[Row]


async def _rows(conn: _AConn, sql: str, params: dict[str, Any]) -> list[Row]:
    cur = await conn.execute(sql, params)
    return await cur.fetchall()


def _span_surface_sql(alias: str = "sp") -> str:
    return (
        f"substring(c.full_text FROM {alias}.char_start + 1 "
        f"FOR {alias}.char_end - {alias}.char_start)"
    )


async def list_annotation_runs(
    conn: _AConn, document_id: str, *, limit: int = 50, after: str | None = None
) -> list[Row]:
    """Return annotation runs for one document, newest first."""
    cursor = decode_cursor(after, "annotation-runs")
    rows = await _rows(
        conn,
        """
        SELECT id, document_id, layer, tool, tool_version, created_at
        FROM annotations
        WHERE document_id = %(doc)s::uuid
          AND (%(after_created)s::timestamptz IS NULL
               OR created_at < %(after_created)s::timestamptz
               OR (created_at = %(after_created)s::timestamptz AND id > %(after_id)s::ulid))
        ORDER BY created_at DESC, id
        LIMIT %(limit)s
        """,
        {
            "doc": document_id,
            "limit": limit,
            "after_created": cursor["created_at"] if cursor else None,
            "after_id": cursor["id"] if cursor else None,
        },
    )
    for row in rows:
        row["cursor"] = encode_cursor("annotation-runs", created_at=row["created_at"], id=row["id"])
    return rows


async def list_tokens(
    conn: _AConn, document_id: str, *, limit: int = 100, after: str | None = None
) -> list[Row]:
    """Return token rows with morphology features for one document."""
    cursor = decode_cursor(after, "tokens")
    rows = await _rows(
        conn,
        f"""
        SELECT t.id, a.document_id, t.annotation_id, t.ordinal, t.upos, t.xpos,
               t.lexeme_uid, sp.char_start, sp.char_end, {_span_surface_sql()} AS surface,
               COALESCE((
                   SELECT jsonb_agg(jsonb_build_object('feature', tf.feature, 'value', tf.value)
                                    ORDER BY tf.feature)
                   FROM token_feature tf WHERE tf.token_id = t.id
               ), '[]'::jsonb) AS features
        FROM tokens t
        JOIN annotations a ON a.id = t.annotation_id
        JOIN spans sp ON sp.id = t.span_id
        JOIN document_content c ON c.document_id = a.document_id
        WHERE a.document_id = %(doc)s::uuid
          AND (%(after_ord)s::int IS NULL OR t.ordinal > %(after_ord)s::int
               OR (t.ordinal = %(after_ord)s::int AND t.id > %(after_id)s::ulid))
        ORDER BY t.ordinal, t.id
        LIMIT %(limit)s
        """,
        {
            "doc": document_id,
            "limit": limit,
            "after_ord": cursor["ordinal"] if cursor else None,
            "after_id": cursor["id"] if cursor else None,
        },
    )
    for row in rows:
        row["cursor"] = encode_cursor("tokens", ordinal=row["ordinal"], id=row["id"])
    return rows


async def list_dependencies(
    conn: _AConn, document_id: str, *, limit: int = 100, after: str | None = None
) -> list[Row]:
    """Return dependency edges for one document."""
    cursor = decode_cursor(after, "dependencies")
    rows = await _rows(
        conn,
        """
        SELECT d.annotation_id, d.dependent_token_id, d.head_token_id, d.deprel
        FROM dependency d
        JOIN annotations a ON a.id = d.annotation_id
        WHERE a.document_id = %(doc)s::uuid
          AND (%(after_dep)s::ulid IS NULL OR d.dependent_token_id > %(after_dep)s::ulid)
        ORDER BY d.dependent_token_id
        LIMIT %(limit)s
        """,
        {"doc": document_id, "limit": limit, "after_dep": cursor["dependent"] if cursor else None},
    )
    for row in rows:
        row["cursor"] = encode_cursor("dependencies", dependent=row["dependent_token_id"])
    return rows


async def list_chunks(
    conn: _AConn, document_id: str, *, limit: int = 100, after: str | None = None
) -> list[Row]:
    """Return shallow chunk spans for one document."""
    cursor = decode_cursor(after, "chunks")
    rows = await _rows(
        conn,
        f"""
        SELECT ch.id, ch.annotation_id, ch.chunk_type, sp.char_start, sp.char_end,
               {_span_surface_sql()} AS surface
        FROM chunk ch
        JOIN annotations a ON a.id = ch.annotation_id
        JOIN spans sp ON sp.id = ch.span_id
        JOIN document_content c ON c.document_id = a.document_id
        WHERE a.document_id = %(doc)s::uuid
          AND (%(after_start)s::int IS NULL OR sp.char_start > %(after_start)s::int
               OR (sp.char_start = %(after_start)s::int AND ch.id > %(after_id)s::ulid))
        ORDER BY sp.char_start, ch.id
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
        row["cursor"] = encode_cursor("chunks", char_start=row["char_start"], id=row["id"])
    return rows


async def list_coref_chains(
    conn: _AConn, document_id: str, *, limit: int = 50, after: str | None = None
) -> list[Row]:
    """Return coreference chains with mention surfaces for one document."""
    cursor = decode_cursor(after, "coref-chains")
    rows = await _rows(
        conn,
        f"""
        SELECT cc.id, cc.annotation_id, cc.document_id,
               COALESCE(jsonb_agg(
                   jsonb_build_object('span_id', cm.span_id, 'surface', {_span_surface_sql()})
                   ORDER BY sp.char_start
               ) FILTER (WHERE cm.span_id IS NOT NULL), '[]'::jsonb) AS mentions
        FROM coref_chain cc
        LEFT JOIN coref_mention cm ON cm.chain_id = cc.id
        LEFT JOIN spans sp ON sp.id = cm.span_id
        LEFT JOIN document_content c ON c.document_id = cc.document_id
        WHERE cc.document_id = %(doc)s::uuid
          AND (%(after_id)s::ulid IS NULL OR cc.id > %(after_id)s::ulid)
        GROUP BY cc.id, cc.annotation_id, cc.document_id
        ORDER BY cc.id
        LIMIT %(limit)s
        """,
        {"doc": document_id, "limit": limit, "after_id": cursor["id"] if cursor else None},
    )
    for row in rows:
        row["cursor"] = encode_cursor("coref-chains", id=row["id"])
    return rows


async def list_entity_links(
    conn: _AConn, document_id: str, *, limit: int = 100, after: str | None = None
) -> list[Row]:
    """Return entity-link rows with mention surfaces for one document."""
    cursor = decode_cursor(after, "entity-links")
    rows = await _rows(
        conn,
        f"""
        SELECT em.id AS mention_id, el.scheme, el.kb_id, el.confidence, em.entity_type,
               sp.char_start, sp.char_end, {_span_surface_sql()} AS surface
        FROM entity_link el
        JOIN entity_mention em ON em.id = el.mention_id
        JOIN annotations a ON a.id = em.annotation_id
        JOIN spans sp ON sp.id = em.span_id
        JOIN document_content c ON c.document_id = a.document_id
        WHERE a.document_id = %(doc)s::uuid
          AND (%(after_start)s::int IS NULL OR sp.char_start > %(after_start)s::int
               OR (sp.char_start = %(after_start)s::int AND em.id > %(after_mention)s::ulid)
               OR (sp.char_start = %(after_start)s::int AND em.id = %(after_mention)s::ulid
                   AND el.scheme > %(after_scheme)s))
        ORDER BY sp.char_start, em.id, el.scheme
        LIMIT %(limit)s
        """,
        {
            "doc": document_id,
            "limit": limit,
            "after_start": cursor["char_start"] if cursor else None,
            "after_mention": cursor["mention_id"] if cursor else None,
            "after_scheme": cursor["scheme"] if cursor else None,
        },
    )
    for row in rows:
        row["cursor"] = encode_cursor(
            "entity-links",
            char_start=row["char_start"],
            mention_id=row["mention_id"],
            scheme=row["scheme"],
        )
    return rows
