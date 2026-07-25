"""Async read adapter for semantic sentence retrieval (NLP Layer 20).

Ranks a document's — or the whole corpus's — sentences by cosine similarity to a
pre-embedded query vector, over the learned ``embedding`` table. Kept in its own
module (mirroring ``persistence_visual_read``) so the semantic reader can grow
without pushing the NLP reader past the D-015 size cap.

RLS-scoped: only sentences in documents the caller may read are visible, via the
existing policies on ``sentences`` / ``spans`` — never a bypass.
"""

from __future__ import annotations

from typing import Any

import psycopg

Row = dict[str, Any]
_AConn = psycopg.AsyncConnection[Row]


async def semantic_search_sentences(
    conn: _AConn,
    query_vector: str,
    model: str,
    *,
    document_id: str | None = None,
    limit: int = 25,
) -> list[Row]:
    """Return sentences ranked by cosine similarity to a pre-embedded query vector.

    ``query_vector`` is a pgvector literal produced by the SAME model as the
    stored rows; ``model`` filters ``embedding`` to that model so a residual row
    from any prior model can never surface. RLS scopes the result to sentences
    the caller may read.
    """
    # HNSW returns a bounded candidate set BEFORE the RLS/model filters apply, so a
    # plain top-N can silently under-return for a non-admin principal. pgvector 0.8+
    # iterative scan keeps scanning the index until LIMIT rows pass the filters.
    async with conn.transaction():
        await conn.execute("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")
        cur = await conn.execute(
            """
            SELECT s.id AS sentence_id, s.document_id, s.ordinal, s.text,
                   1 - (e.vector <=> %(q)s::vector) AS similarity
            FROM embedding e
            JOIN sentences s ON s.span_id = e.span_id
            WHERE e.model = %(model)s
              AND (%(doc)s::uuid IS NULL OR s.document_id = %(doc)s::uuid)
            ORDER BY e.vector <=> %(q)s::vector
            LIMIT %(limit)s
            """,
            {"q": query_vector, "model": model, "doc": document_id, "limit": limit},
        )
        return await cur.fetchall()
