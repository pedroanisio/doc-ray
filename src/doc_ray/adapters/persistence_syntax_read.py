"""Read adapters for Layers 9 (constituency) and 14 (SRL).

Split from :mod:`doc_ray.adapters.persistence_layers_read` for the D-015 net-LOC
gate. Both layers are written by ``adapters/annotation_syntax_rows`` under the
document's spaCy annotation run; these are the only read paths, so a caller never
touches ``constituent`` / ``srl_predicate`` / ``srl_argument`` directly.

Surfaces are reconstructed from ``document_content.full_text`` at the row's own
offsets rather than stored twice — the same stand-off convention every other layer
reader uses, so a text correction cannot leave a layer disagreeing with the text.
"""

from __future__ import annotations

from doc_ray.adapters.persistence_layers_read import (
    Row,
    _AConn,
    _rows,
    _span_surface_sql,
)
from doc_ray.adapters.read_cursors import decode_cursor, encode_cursor


async def list_constituents(
    conn: _AConn, document_id: str, *, limit: int = 200, after: str | None = None
) -> list[Row]:
    """Return phrase-structure nodes for one document, outermost first.

    Ordered by ``(char_start, -char_end)`` so a parent always precedes its children
    and a client can rebuild the tree by streaming, without holding the whole
    document's nodes in memory.

    Args:
        conn: Async database connection.
        document_id: The document to read.
        limit: Maximum nodes to return.
        after: Opaque cursor from a previous page.

    Returns:
        Rows with ``id``, ``annotation_id``, ``parent_id``, ``label``, offsets, the
        reconstructed ``surface``, and a ``cursor``.
    """
    cursor = decode_cursor(after, "constituents")
    rows = await _rows(
        conn,
        f"""
        SELECT k.id, k.annotation_id, k.parent_id, k.label,
               sp.char_start, sp.char_end, {_span_surface_sql()} AS surface
        FROM constituent k
        JOIN annotations a ON a.id = k.annotation_id
        JOIN spans sp ON sp.id = k.span_id
        JOIN document_content c ON c.document_id = a.document_id
        WHERE a.document_id = %(doc)s::uuid
          AND (%(after_start)s::int IS NULL OR sp.char_start > %(after_start)s::int
               OR (sp.char_start = %(after_start)s::int AND k.id > %(after_id)s::ulid))
        ORDER BY sp.char_start, sp.char_end DESC, k.id
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
        row["cursor"] = encode_cursor("constituents", char_start=row["char_start"], id=row["id"])
    return rows


async def list_srl_frames(
    conn: _AConn, document_id: str, *, limit: int = 100, after: str | None = None
) -> list[Row]:
    """Return predicate-argument frames for one document, in document order.

    Arguments are aggregated into each frame rather than paged separately: a frame
    without its roles is not a usable answer, and the fan-out is bounded by the
    predicate's own dependents.

    Args:
        conn: Async database connection.
        document_id: The document to read.
        limit: Maximum frames to return.
        after: Opaque cursor from a previous page.

    Returns:
        Rows with ``id``, ``annotation_id``, ``frame``, the predicate's offsets and
        surface, an ``arguments`` JSON array (``role``, offsets, ``surface``), and a
        ``cursor``.
    """
    cursor = decode_cursor(after, "srl-frames")
    rows = await _rows(
        conn,
        f"""
        SELECT p.id, p.annotation_id, p.frame,
               psp.char_start, psp.char_end,
               {_span_surface_sql("psp")} AS surface,
               COALESCE(jsonb_agg(
                   jsonb_build_object(
                       'role', arg.role,
                       'char_start', asp.char_start,
                       'char_end', asp.char_end,
                       'surface', {_span_surface_sql("asp")}
                   ) ORDER BY asp.char_start
               ) FILTER (WHERE arg.span_id IS NOT NULL), '[]'::jsonb) AS arguments
        FROM srl_predicate p
        JOIN annotations a ON a.id = p.annotation_id
        JOIN tokens t ON t.id = p.token_id
        JOIN spans psp ON psp.id = t.span_id
        JOIN document_content c ON c.document_id = a.document_id
        LEFT JOIN srl_argument arg ON arg.predicate_id = p.id
        LEFT JOIN spans asp ON asp.id = arg.span_id
        WHERE a.document_id = %(doc)s::uuid
          AND (%(after_start)s::int IS NULL OR psp.char_start > %(after_start)s::int
               OR (psp.char_start = %(after_start)s::int AND p.id > %(after_id)s::ulid))
        GROUP BY p.id, p.annotation_id, p.frame, psp.char_start, psp.char_end, c.full_text
        ORDER BY psp.char_start, p.id
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
        row["cursor"] = encode_cursor("srl-frames", char_start=row["char_start"], id=row["id"])
    return rows
