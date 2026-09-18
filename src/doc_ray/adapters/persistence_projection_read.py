"""Async read adapter for the projection engine (issue #8).

Serves the GraphQL ``entityProjection`` read. Every read is bounded and
keyset-paginated, and reads only derived tables (``entity_projection`` joined to
``canonical``/mentions for display) — never a live aggregate over a fact table
(D-024). RLS on those tables scopes every row to what the caller may read; this
adapter adds no authorization of its own. (The per-lens entity landscape reads moved
to :mod:`doc_ray.adapters.persistence_lens_rollup_read` in the hyper-context rollups, #10.)
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


async def _row(conn: _AConn, sql: str, params: dict[str, Any]) -> Row | None:
    cur = await conn.execute(sql, params)
    return await cur.fetchone()


async def resolve_effective_lens(
    conn: _AConn, document_id: str, lens_id: str | None
) -> tuple[str | None, bool, bool]:
    """Resolve which lens a lens-less read uses: ``(lens_id, is_default, ambiguous)``.

    Explicit ``lens_id`` wins. Otherwise the document's ``default_lens_id`` is used —
    but only if it is still a live projecting scope (PALS's Law: The lens registry allows a
    dangling default). Failing that, it falls back to the reserved ``global`` lens
    and flags ``ambiguous=True`` when the document belongs to more than one projecting
    scope lens with no default (D-4 — surface the ambiguity, never pick arbitrarily).
    Returns ``(None, False, False)`` if there is no ``global`` lens (never in practice).
    """
    if lens_id is not None:
        return (lens_id, False, False)

    doc = await _row(
        conn, "SELECT default_lens_id FROM documents WHERE id = %(d)s::uuid", {"d": document_id}
    )
    default = str(doc["default_lens_id"]) if doc and doc["default_lens_id"] else None
    if default is not None:
        live = await _row(
            conn,
            "SELECT 1 FROM document_collections WHERE id = %(l)s::uuid AND is_scope AND projects",
            {"l": default},
        )
        if live is not None:
            return (default, True, False)

    gl = await _row(conn, "SELECT id FROM document_collections WHERE name = 'global'", {})
    if gl is None:
        return (None, False, False)
    members = await _rows(
        conn,
        "SELECT c.id FROM document_collection_members m "
        "JOIN document_collections c ON c.id = m.collection_id "
        "WHERE m.document_id = %(d)s::uuid AND c.is_scope AND c.projects LIMIT 2",
        {"d": document_id},
    )
    return (str(gl["id"]), False, len(members) > 1)


_PROJECTION_SQL = """
    SELECT ep.mention_id, ep.lens_id, ep.document_id, ep.canonical_id, ep.scope,
           ep.method, ep.confidence, ep.status, ep.refreshed_at,
           em.surface, em.entity_type AS mention_type, sp.char_start, sp.char_end,
           c.preferred, c.entity_type AS canonical_type, c.wikidata_id
    FROM entity_projection ep
    JOIN entity_mention em ON em.id = ep.mention_id
    JOIN spans sp ON sp.id = em.span_id
    LEFT JOIN canonical c ON c.id = ep.canonical_id
    WHERE ep.document_id = %(doc)s::uuid AND ep.lens_id = %(lens)s::uuid
      AND (%(after)s::ulid IS NULL OR ep.mention_id > %(after)s::ulid)
    ORDER BY ep.mention_id
    LIMIT %(limit)s
"""


async def entity_projection(
    conn: _AConn, document_id: str, lens_id: str, *, limit: int = 100, after: str | None = None
) -> list[Row]:
    """Return a document's projection rows within one lens, keyset-paginated by mention."""
    cursor = decode_cursor(after, "entity-projection")
    rows = await _rows(
        conn,
        _PROJECTION_SQL,
        {
            "doc": document_id,
            "lens": lens_id,
            "after": cursor["mention_id"] if cursor else None,
            "limit": limit,
        },
    )
    for row in rows:
        row["cursor"] = encode_cursor("entity-projection", mention_id=row["mention_id"])
    return rows


async def mention_span(conn: _AConn, mention_id: str) -> Row | None:
    """Return a mention's ``(document_id, char_start, char_end, entity_type)`` or None.

    The stable span identity a projection review is keyed by (survives re-annotation).
    RLS on ``entity_mention`` scopes this to documents the caller may read.
    """
    return await _row(
        conn,
        "SELECT a.document_id, sp.char_start, sp.char_end, em.entity_type "
        "FROM entity_mention em "
        "JOIN annotations a ON a.id = em.annotation_id "
        "JOIN spans sp ON sp.id = em.span_id "
        "WHERE em.id = %(m)s::ulid",
        {"m": mention_id},
    )


async def record_projection_review(
    conn: _AConn,
    *,
    document_id: str,
    lens_id: str,
    char_start: int,
    char_end: int,
    entity_type: str,
    decision: str,
    reviewer: str | None,
    rationale: str | None,
) -> None:
    """Record a human confirm/reject as a proposal keyed by the stable span.

    Written by the API (docray_client grant); the batch refresh applies it onto
    ``entity_projection.status``. RLS WITH CHECK requires the document be writable.
    """
    await conn.execute(
        "INSERT INTO entity_projection_review "
        "(document_id, lens_id, char_start, char_end, entity_type, decision, reviewer, rationale) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (document_id, lens_id, char_start, char_end, entity_type, decision, reviewer, rationale),
    )
