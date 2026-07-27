"""Language-aware document full-text search read adapter."""

from __future__ import annotations

from typing import Any, Literal

import psycopg

Row = dict[str, Any]
_AConn = psycopg.AsyncConnection[Row]
FullTextSearchMode = Literal["terms", "phrase"]


async def _rows(conn: _AConn, sql: str, params: tuple[Any, ...]) -> list[Row]:
    cur = await conn.execute(sql, params)
    return await cur.fetchall()


async def fulltext_search(
    conn: _AConn,
    query: str,
    *,
    limit: int = 25,
    mode: FullTextSearchMode = "terms",
) -> list[Row]:
    """Return ranked Documents using all-term or ordered-phrase matching.

    Both safe parsers use the same per-Document ``docray_ts_config`` that built
    ``document_content.fts``. Caller text stays a bound parameter; raw tsquery
    syntax is never accepted.
    """
    parsers = {"terms": "plainto_tsquery", "phrase": "phraseto_tsquery"}
    try:
        parser = parsers[mode]
    except KeyError as exc:
        raise ValueError("full-text mode must be 'terms' or 'phrase'") from exc
    return await _rows(
        conn,
        "SELECT d.id AS doc_id, d.title, ts_rank(c.fts, q) AS rank "
        "FROM document_content c JOIN documents d ON d.id = c.document_id, "
        f"     {parser}(docray_ts_config(d.language), %s) q "
        "WHERE c.fts @@ q ORDER BY rank DESC, d.title LIMIT %s",
        (query, limit),
    )
