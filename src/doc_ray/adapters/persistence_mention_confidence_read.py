"""Async reader for the materialized mention-confidence review queue."""

from __future__ import annotations

from typing import Any

import psycopg

from doc_ray.adapters.read_cursors import decode_cursor, encode_cursor

Row = dict[str, Any]
_AConn = psycopg.AsyncConnection[Row]


async def uncertain_mentions(
    conn: _AConn,
    document_id: str,
    *,
    entity_type: str | None = None,
    limit: int = 100,
    after: str | None = None,
) -> list[Row]:
    """Return unreviewed mentions ranked from least to most trustworthy.

    Confidence and explanations are read from the materialized columns written
    by ``score_mentions``. The query deliberately performs no corpus aggregate.
    A durable correction covering the same stable document span removes the
    mention from this queue, regardless of the correction action.
    """
    cursor = decode_cursor(after, "uncertain-mentions")
    cur = await conn.execute(
        """
        SELECT em.id, an.document_id, sp.char_start, sp.char_end,
               em.surface, em.entity_type, em.confidence,
               em.confidence_reasons, em.confidence_method
        FROM entity_mention em
        JOIN annotations an ON an.id = em.annotation_id
        JOIN spans sp ON sp.id = em.span_id
        WHERE an.document_id = %(doc)s::uuid
          AND em.confidence IS NOT NULL
          AND (%(entity_type)s::text IS NULL OR em.entity_type = %(entity_type)s)
          AND NOT EXISTS (
              SELECT 1
              FROM entity_mention_correction c
              WHERE c.document_id = an.document_id
                AND c.char_start = sp.char_start
                AND c.char_end = sp.char_end
          )
          AND (%(after_confidence)s::real IS NULL
               OR (em.confidence, em.id)
                  > (%(after_confidence)s::real, %(after_id)s::ulid))
        ORDER BY em.confidence, em.id
        LIMIT %(limit)s
        """,
        {
            "doc": document_id,
            "entity_type": entity_type,
            "limit": limit,
            "after_confidence": cursor["confidence"] if cursor else None,
            "after_id": cursor["id"] if cursor else None,
        },
    )
    rows = await cur.fetchall()
    for row in rows:
        row["cursor"] = encode_cursor(
            "uncertain-mentions", confidence=row["confidence"], id=row["id"]
        )
    return rows
