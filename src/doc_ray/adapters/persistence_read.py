"""Async read adapter — the shared, read-only query surface (D-002).

Implements :class:`doc_ray.ports.repository.ReadRepository` over an async psycopg
connection. The queries here are the single source of truth for reads: they are
lifted from the previously-duplicated SQL in ``adapters/export.py`` (the nested
document record) and ``webapp/app.py`` (document list, keyword-in-context, lexeme
and sentence search), so the GraphQL API, the Flask browser, and the export CLI can
all share one definition and never drift.

Every function takes an ``AsyncConnection`` created with ``row_factory=dict_row``;
the caller owns the connection (a pool in the API layer). Nothing here opens,
closes, or commits — reads run under the connection's autocommit mode.
"""

from __future__ import annotations

from typing import Any

import psycopg

Row = dict[str, Any]
_AConn = psycopg.AsyncConnection[Row]


async def _rows(conn: _AConn, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> list[Row]:
    """Execute *sql* and return all rows as dicts."""
    cur = await conn.execute(sql, params)
    return await cur.fetchall()


async def _row(conn: _AConn, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> Row | None:
    """Execute *sql* and return the first row as a dict, or ``None``."""
    cur = await conn.execute(sql, params)
    return await cur.fetchone()


def _split_kwic(text: str, needle: str) -> tuple[str, str, str]:
    """Split *text* around the first occurrence of *needle* into (pre, hit, post)."""
    pre, sep, post = text.partition(needle)
    return (pre, needle if sep else "", post)


# --------------------------------------------------------------------------
# Documents
# --------------------------------------------------------------------------


async def get_document(conn: _AConn, document_id: str) -> Row | None:
    """Return one document with its 4NF satellites attached, or ``None``.

    The returned row carries ``authors``, ``tags``, ``identifiers`` (list of
    ``{scheme, value}``), and ``metadata`` (a ``{key: value}`` mapping) reassembled
    from their junction tables.

    Args:
        conn: Async database connection.
        document_id: UUID of the document.

    Returns:
        The document row with satellites, or ``None`` when the id is unknown.
    """
    document = await _row(conn, "SELECT * FROM documents WHERE id = %s", (document_id,))
    if document is None:
        return None
    document["authors"] = [
        r["name"]
        for r in await _rows(
            conn,
            "SELECT a.name FROM document_authors da JOIN authors a ON a.id = da.author_id "
            "WHERE da.document_id = %s ORDER BY da.position",
            (document_id,),
        )
    ]
    document["tags"] = [
        r["tag"]
        for r in await _rows(
            conn,
            "SELECT t.tag FROM document_tags dt JOIN tags t ON t.id = dt.tag_id "
            "WHERE dt.document_id = %s ORDER BY t.tag",
            (document_id,),
        )
    ]
    document["identifiers"] = await _rows(
        conn,
        "SELECT scheme, value FROM document_identifiers WHERE document_id = %s ORDER BY scheme",
        (document_id,),
    )
    document["metadata"] = {
        r["key"]: r["value"]
        for r in await _rows(
            conn,
            "SELECT key, value FROM document_metadata WHERE document_id = %s ORDER BY key",
            (document_id,),
        )
    }
    return document


async def list_documents(conn: _AConn, *, limit: int = 50, offset: int = 0) -> list[Row]:
    """Return document summary rows (with child counts), newest first.

    Args:
        conn: Async database connection.
        limit: Maximum number of documents to return.
        offset: Number of documents to skip (paging).

    Returns:
        Summary rows with ``n_sentences`` and ``n_provenance`` counts.
    """
    return await _rows(
        conn,
        """
        SELECT d.id, d.title, d.original_filename AS filename, d.doc_type, d.language,
               d.page_count, d.char_count, d.status, d.created_at,
               (SELECT count(*) FROM sentences s WHERE s.document_id = d.id) AS n_sentences,
               (SELECT count(*) FROM document_provenance p WHERE p.document_id = d.id)
                   AS n_provenance
        FROM documents d
        ORDER BY d.created_at DESC, d.id
        LIMIT %s OFFSET %s
        """,
        (limit, offset),
    )


async def list_sentences(
    conn: _AConn, document_id: str, *, limit: int | None = None, offset: int = 0
) -> list[Row]:
    """Return a document's sentences (with span offsets) in ordinal order.

    Args:
        conn: Async database connection.
        document_id: UUID of the owning document.
        limit: Optional cap on the number of sentences returned.
        offset: Number of sentences to skip (paging).

    Returns:
        Rows of ``{id, ordinal, text, char_start, char_end}``.
    """
    sql = (
        "SELECT s.id, s.ordinal, s.text, sp.char_start, sp.char_end "
        "FROM sentences s JOIN spans sp ON sp.id = s.span_id "
        "WHERE s.document_id = %s ORDER BY s.ordinal"
    )
    params: tuple[Any, ...] = (document_id,)
    if limit is not None:
        sql += " LIMIT %s OFFSET %s"
        params = (document_id, limit, offset)
    return await _rows(conn, sql, params)


async def get_provenance(conn: _AConn, document_id: str) -> list[Row]:
    """Return a document's provenance events, params reassembled, in ULID order.

    Args:
        conn: Async database connection.
        document_id: UUID of the owning document.

    Returns:
        Event rows each carrying a ``params`` dict of its EAV parameters.
    """
    events = await _rows(
        conn,
        "SELECT id, kind, tool, tool_version, occurred_at, actor, "
        "       input_sha256, output_sha256, note "
        "FROM document_provenance WHERE document_id = %s ORDER BY id",
        (document_id,),
    )
    param_rows = await _rows(
        conn,
        "SELECT pp.event_id, pp.key, pp.value FROM provenance_params pp "
        "JOIN document_provenance dp ON dp.id = pp.event_id WHERE dp.document_id = %s",
        (document_id,),
    )
    by_event: dict[str, dict[str, Any]] = {}
    for r in param_rows:
        by_event.setdefault(str(r["event_id"]), {})[r["key"]] = r["value"]
    for event in events:
        event["params"] = by_event.get(str(event["id"]), {})
    return events


# --------------------------------------------------------------------------
# Batched by-document readers (feed the API DataLoaders; avoid N+1)
# --------------------------------------------------------------------------


async def authors_by_documents(conn: _AConn, document_ids: list[str]) -> dict[str, list[str]]:
    """Return ``{document_id: [author, ...]}`` for every id (ordered by position)."""
    out: dict[str, list[str]] = {str(i): [] for i in document_ids}
    rows = await _rows(
        conn,
        "SELECT da.document_id, a.name FROM document_authors da "
        "JOIN authors a ON a.id = da.author_id "
        "WHERE da.document_id = ANY(%s::uuid[]) ORDER BY da.document_id, da.position",
        (document_ids,),
    )
    for r in rows:
        out[str(r["document_id"])].append(r["name"])
    return out


async def tags_by_documents(conn: _AConn, document_ids: list[str]) -> dict[str, list[str]]:
    """Return ``{document_id: [tag, ...]}`` for every id (alphabetical)."""
    out: dict[str, list[str]] = {str(i): [] for i in document_ids}
    rows = await _rows(
        conn,
        "SELECT dt.document_id, t.tag FROM document_tags dt "
        "JOIN tags t ON t.id = dt.tag_id "
        "WHERE dt.document_id = ANY(%s::uuid[]) ORDER BY dt.document_id, t.tag",
        (document_ids,),
    )
    for r in rows:
        out[str(r["document_id"])].append(r["tag"])
    return out


async def metadata_by_documents(conn: _AConn, document_ids: list[str]) -> dict[str, dict[str, str]]:
    """Return ``{document_id: {key: value}}`` metadata for every id."""
    out: dict[str, dict[str, str]] = {str(i): {} for i in document_ids}
    rows = await _rows(
        conn,
        "SELECT document_id, key, value FROM document_metadata "
        "WHERE document_id = ANY(%s::uuid[]) ORDER BY document_id, key",
        (document_ids,),
    )
    for r in rows:
        out[str(r["document_id"])][r["key"]] = r["value"]
    return out


async def sentence_counts_by_documents(conn: _AConn, document_ids: list[str]) -> dict[str, int]:
    """Return ``{document_id: sentence_count}`` for every id (0 when none)."""
    out: dict[str, int] = {str(i): 0 for i in document_ids}
    rows = await _rows(
        conn,
        "SELECT document_id, count(*) AS n FROM sentences "
        "WHERE document_id = ANY(%s::uuid[]) GROUP BY document_id",
        (document_ids,),
    )
    for r in rows:
        out[str(r["document_id"])] = int(r["n"])
    return out


# --------------------------------------------------------------------------
# Lexemes
# --------------------------------------------------------------------------


async def get_lexeme(conn: _AConn, uid: str) -> Row | None:
    """Return one lexeme by uid, or ``None`` if it does not exist."""
    return await _row(conn, "SELECT * FROM lexemes WHERE uid = %s", (uid,))


async def list_lexemes(
    conn: _AConn,
    *,
    q: str | None = None,
    pos: str | None = None,
    lang: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Row]:
    """Return lexemes filtered by lemma substring / pos / lang, busiest first.

    Args:
        conn: Async database connection.
        q: Case-insensitive lemma substring, or ``None`` for no filter.
        pos: Exact part-of-speech filter, or ``None``.
        lang: Exact language filter, or ``None``.
        limit: Maximum rows to return.
        offset: Rows to skip (paging).

    Returns:
        Rows of ``{uid, lang, pos, lemma, uses}`` ordered by use count then lemma.
    """
    return await _rows(
        conn,
        """
        SELECT l.uid, l.lang, l.pos, l.lemma, count(o.lexeme_uid) AS uses
        FROM lexemes l
        LEFT JOIN lexeme_occurrences o ON o.lexeme_uid = l.uid
        WHERE (%(q)s = '' OR l.lemma ILIKE %(qpat)s)
          AND (%(pos)s = '' OR l.pos = %(pos)s)
          AND (%(lang)s = '' OR l.lang = %(lang)s)
        GROUP BY l.uid, l.lang, l.pos, l.lemma
        ORDER BY uses DESC, l.lemma
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        {
            "q": q or "",
            "qpat": f"%{q}%" if q else "",
            "pos": pos or "",
            "lang": lang or "",
            "limit": limit,
            "offset": offset,
        },
    )


async def lexeme_kwic(conn: _AConn, uid: str, *, limit: int = 80) -> list[Row]:
    """Return keyword-in-context occurrences for a lexeme.

    Each row carries the ±45-character ``kwic`` window plus a ``pre``/``hit``/``post``
    split around the surface form for highlighting.

    Args:
        conn: Async database connection.
        uid: Lexeme uid.
        limit: Maximum occurrences to return.

    Returns:
        Rows of ``{doc_id, title, char_start, surface, kwic, pre, hit, post}``.
    """
    rows = await _rows(
        conn,
        """
        SELECT d.id AS doc_id, d.title, s.char_start,
               substring(c.full_text FROM greatest(s.char_start - 45, 0) + 1
                         FOR (s.char_end - s.char_start) + 90) AS kwic,
               substring(c.full_text FROM s.char_start + 1
                         FOR s.char_end - s.char_start) AS surface
        FROM lexeme_occurrences o
        JOIN annotations a      ON a.id = o.annotation_id
        JOIN spans s            ON s.id = o.span_id
        JOIN documents d        ON d.id = a.document_id
        JOIN document_content c ON c.document_id = d.id
        WHERE o.lexeme_uid = %s
        ORDER BY d.title, s.char_start
        LIMIT %s
        """,
        (uid, limit),
    )
    for r in rows:
        r["pre"], r["hit"], r["post"] = _split_kwic(r["kwic"] or "", r["surface"] or "")
    return rows


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------


async def search_sentences(conn: _AConn, term: str, *, limit: int = 100) -> list[Row]:
    """Return sentences containing *term* (case-insensitive substring).

    Args:
        conn: Async database connection.
        term: Substring to match against sentence text.
        limit: Maximum sentences to return.

    Returns:
        Rows of ``{doc_id, title, ordinal, text, pre, hit, post}``.
    """
    rows = await _rows(
        conn,
        "SELECT d.id AS doc_id, d.title, s.ordinal, s.text "
        "FROM sentences s JOIN documents d ON d.id = s.document_id "
        "WHERE s.text ILIKE %s ORDER BY d.title, s.ordinal LIMIT %s",
        (f"%{term}%", limit),
    )
    for r in rows:
        r["pre"], r["hit"], r["post"] = _split_kwic(r["text"], term)
    return rows


async def fulltext_search(conn: _AConn, query: str, *, limit: int = 25) -> list[Row]:
    """Return documents ranked by full-text match against ``document_content.fts``.

    Args:
        conn: Async database connection.
        query: A ``plainto_tsquery`` search string (English configuration).
        limit: Maximum documents to return.

    Returns:
        Rows of ``{doc_id, title, rank}`` ordered by descending rank.
    """
    return await _rows(
        conn,
        "SELECT d.id AS doc_id, d.title, ts_rank(c.fts, q) AS rank "
        "FROM document_content c JOIN documents d ON d.id = c.document_id, "
        "     plainto_tsquery('english', %s) q "
        "WHERE c.fts @@ q ORDER BY rank DESC, d.title LIMIT %s",
        (query, limit),
    )
