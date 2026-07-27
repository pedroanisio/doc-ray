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

from doc_ray.adapters.persistence_fulltext_read import (
    FullTextSearchMode as FullTextSearchMode,
)
from doc_ray.adapters.persistence_fulltext_read import fulltext_search as fulltext_search
from doc_ray.adapters.persistence_read_collections import (
    CollectionRuleInput as CollectionRuleInput,
)
from doc_ray.adapters.persistence_read_collections import (
    collection_rule_summary as collection_rule_summary,
)
from doc_ray.adapters.persistence_read_collections import (
    collections_for_document as collections_for_document,
)
from doc_ray.adapters.persistence_read_collections import (
    get_collection as get_collection,
)
from doc_ray.adapters.persistence_read_collections import (
    list_collection_documents as list_collection_documents,
)
from doc_ray.adapters.persistence_read_collections import (
    list_collections as list_collections,
)
from doc_ray.adapters.persistence_read_collections import (
    list_lenses as list_lenses,
)
from doc_ray.adapters.read_cursors import decode_cursor, encode_cursor
from doc_ray.adapters.sentence_search_sql import match_clause
from doc_ray.domain.search_terms import kwic as _split_kwic

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


#: SQL fragment mapping a document id onto its shard (0..N-1) for corpus
#: partitioning. ``%`` can go negative in Postgres, so normalize into range.
#: ``hashtext`` is deterministic within a Postgres major version, so a document
#: always lands in the same shard — the property that makes N-agent map-reduce
#: complete and disjoint.
_SHARD_EXPR = "((hashtext(d.id::text) %% %(shard_count)s) + %(shard_count)s) %% %(shard_count)s"


async def list_documents(
    conn: _AConn,
    *,
    limit: int = 50,
    after: str | None = None,
    doc_type: str | None = None,
    status: str | None = None,
    tags: list[str] | None = None,
    shard_index: int | None = None,
    shard_count: int | None = None,
) -> list[Row]:
    """Return document summary rows (with child counts), newest first.

    Args:
        conn: Async database connection.
        limit: Maximum number of documents to return.
        after: Optional keyset cursor returned by the previous page.
        doc_type: Optional exact ``doc_type`` filter (e.g. ``"pdf"``).
        status: Optional exact ``status`` filter (e.g. ``"extracted"``).
        tags: Optional tag filter — a document matches if it has any of these tags.
        shard_index: When paired with *shard_count*, restrict to documents whose
            ``hashtext(id) mod shard_count`` equals this index (0-based). Used to
            partition the corpus into disjoint slices for parallel processing.
        shard_count: Total number of shards; required with *shard_index*.

    Returns:
        Summary rows with ``n_sentences`` and ``n_provenance`` counts.
    """
    cursor = decode_cursor(after, "documents")
    shard_filter = f"AND {_SHARD_EXPR} = %(shard_index)s" if shard_count is not None else ""
    rows = await _rows(
        conn,
        f"""
        SELECT d.id, d.title, d.original_filename AS filename, d.doc_type, d.language,
               d.page_count, d.char_count, d.status, d.lifecycle_state,
               d.archived_at, d.deleted_at, d.created_at, d.default_lens_id,
               (SELECT n_sentences FROM document_stats ds WHERE ds.document_id = d.id)
                   AS n_sentences,
               (SELECT count(*) FROM document_provenance p WHERE p.document_id = d.id)
                   AS n_provenance
        FROM documents d
        WHERE (%(after_created)s::timestamptz IS NULL
               OR d.created_at < %(after_created)s::timestamptz
               OR (d.created_at = %(after_created)s::timestamptz
                   AND d.id > %(after_id)s::uuid))
          AND (%(doc_type)s::text IS NULL OR d.doc_type = %(doc_type)s)
          AND (%(status)s::text IS NULL OR d.status = %(status)s)
          AND (%(tags)s::text[] IS NULL OR EXISTS (
                SELECT 1 FROM document_tags dt JOIN tags t ON t.id = dt.tag_id
                WHERE dt.document_id = d.id AND t.tag = ANY(%(tags)s::text[])
              ))
          {shard_filter}
        ORDER BY d.created_at DESC, d.id
        LIMIT %(limit)s
        """,
        {
            "limit": limit,
            "after_created": cursor["created_at"] if cursor else None,
            "after_id": cursor["id"] if cursor else None,
            "doc_type": doc_type,
            "status": status,
            "tags": tags,
            "shard_index": shard_index,
            "shard_count": shard_count,
        },
    )
    for row in rows:
        row["cursor"] = encode_cursor("documents", created_at=row["created_at"], id=row["id"])
    return rows


async def corpus_shard_counts(conn: _AConn, shard_count: int) -> list[Row]:
    """Return ``{shard, n}`` — the visible document count in each shard 0..N-1.

    Bounded aggregate over ``documents`` (one row per document, not a large fact
    table — D-024 flags only the multi-million-row tables), RLS-scoped to what the
    caller may read. Shards with zero documents are returned as ``n = 0`` so the
    manifest always lists every shard 0..N-1.
    """
    rows = await _rows(
        conn,
        f"""
        WITH shard_of AS (
            SELECT {_SHARD_EXPR} AS shard FROM documents d
        )
        SELECT g.shard, count(shard_of.shard) AS n
        FROM generate_series(0, %(shard_count)s - 1) AS g(shard)
        LEFT JOIN shard_of ON shard_of.shard = g.shard
        GROUP BY g.shard
        ORDER BY g.shard
        """,
        {"shard_count": shard_count},
    )
    return rows


async def list_sentences(
    conn: _AConn,
    document_id: str,
    *,
    limit: int | None = None,
    after: str | None = None,
    from_ordinal: int | None = None,
    to_ordinal: int | None = None,
    page: int | None = None,
) -> list[Row]:
    """Return a document's sentences (with span offsets) in ordinal order.

    Args:
        conn: Async database connection.
        document_id: UUID of the owning document.
        limit: Optional cap on the number of sentences returned.
        after: Optional keyset cursor returned by the previous page.
        from_ordinal: Optional inclusive lower bound on ``ordinal`` — random
            access to a slice without walking from the start.
        to_ordinal: Optional inclusive upper bound on ``ordinal``.
        page: Optional source-page filter (``spans.page_number``), for reading
            one page's sentences directly.

    Returns:
        Rows of ``{id, ordinal, text, char_start, char_end}``.
    """
    cursor = decode_cursor(after, "sentences")
    sql = (
        "SELECT s.id, s.ordinal, s.text, sp.char_start, sp.char_end "
        "FROM sentences s JOIN spans sp ON sp.id = s.span_id "
        "WHERE s.document_id = %(doc)s "
        "AND (%(after_ord)s::int IS NULL OR s.ordinal > %(after_ord)s::int "
        "     OR (s.ordinal = %(after_ord)s::int AND s.id > %(after_id)s::ulid)) "
        "AND (%(from_ordinal)s::int IS NULL OR s.ordinal >= %(from_ordinal)s::int) "
        "AND (%(to_ordinal)s::int IS NULL OR s.ordinal <= %(to_ordinal)s::int) "
        "AND (%(page)s::int IS NULL OR sp.page_number = %(page)s::int) "
        "ORDER BY s.ordinal, s.id"
    )
    params: dict[str, Any] = {
        "doc": document_id,
        "after_ord": cursor["ordinal"] if cursor else None,
        "after_id": cursor["id"] if cursor else None,
        "from_ordinal": from_ordinal,
        "to_ordinal": to_ordinal,
        "page": page,
    }
    if limit is not None:
        sql += " LIMIT %(limit)s"
        params["limit"] = limit
    rows = await _rows(conn, sql, params)
    for row in rows:
        row["cursor"] = encode_cursor("sentences", ordinal=row["ordinal"], id=row["id"])
    return rows


async def document_has_position_data(conn: _AConn, document_id: str) -> bool:
    """Return whether any span for the document has persisted page geometry."""
    cur = await conn.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM spans
            WHERE document_id = %s AND page_number IS NOT NULL
        ) AS ok
        """,
        (document_id,),
    )
    row = await cur.fetchone()
    return bool(row and row["ok"])


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


async def get_metadata_history(conn: _AConn, document_id: str) -> list[Row]:
    """Return a document's title/description/tags edit history, oldest first (BL-058).

    Args:
        conn: Async database connection.
        document_id: UUID of the owning document.

    Returns:
        Change rows (``field``, ``old_value``, ``new_value``, ``changed_at``,
        ``actor``), in ULID (== time) order.
    """
    return await _rows(
        conn,
        "SELECT id, document_id, field, old_value, new_value, changed_at, actor "
        "FROM document_metadata_history WHERE document_id = %s ORDER BY id",
        (document_id,),
    )


# --------------------------------------------------------------------------
# Document collections
# --------------------------------------------------------------------------


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
    """Return ``{document_id: sentence_count}`` for every id (0 when none).

    Reads the ``document_stats`` rollup (migration 095) instead of aggregating the
    870k-row ``sentences`` table live per request (D-024).
    """
    out: dict[str, int] = {str(i): 0 for i in document_ids}
    rows = await _rows(
        conn,
        "SELECT document_id, n_sentences AS n FROM document_stats "
        "WHERE document_id = ANY(%s::uuid[])",
        (document_ids,),
    )
    for r in rows:
        out[str(r["document_id"])] = int(r["n"])
    return out


async def token_counts_by_documents(conn: _AConn, document_ids: list[str]) -> dict[str, int]:
    """Return ``{document_id: token_count}`` for every id (0 when none).

    Counts rows in ``tokens`` across the document's annotation runs (the pipeline
    writes one token layer per document, so this is the document's token count).
    """
    out: dict[str, int] = {str(i): 0 for i in document_ids}
    rows = await _rows(
        conn,
        "SELECT document_id, n_tokens AS n FROM document_stats WHERE document_id = ANY(%s::uuid[])",
        (document_ids,),
    )
    for r in rows:
        out[str(r["document_id"])] = int(r["n"])
    return out


# --------------------------------------------------------------------------
# Lexemes
# --------------------------------------------------------------------------


async def get_lexeme(conn: _AConn, uid: str) -> Row | None:
    """Return one RLS-visible lexeme by uid, or ``None`` if it does not exist."""
    return await _row(
        conn,
        """
        SELECT l.uid, l.lang, l.pos, l.lemma
        FROM lexemes l
        WHERE l.uid = %s
          AND EXISTS (
              SELECT 1 FROM lexeme_occurrences o
              WHERE o.lexeme_uid = l.uid
          )
        """,
        (uid,),
    )


async def list_lexemes(
    conn: _AConn,
    *,
    q: str | None = None,
    pos: str | None = None,
    lang: str | None = None,
    document_id: str | None = None,
    limit: int = 100,
    after: str | None = None,
) -> list[Row]:
    """Return lexemes filtered by lemma substring / pos / lang, busiest first.

    Args:
        conn: Async database connection.
        q: Case-insensitive lemma substring, or ``None`` for no filter.
        pos: Exact part-of-speech filter, or ``None``.
        lang: Exact language filter, or ``None``.
        document_id: When set, restrict to lexemes occurring in this document —
            ``uses`` then counts occurrences in that document only, not the corpus.
        limit: Maximum rows to return.
        after: Optional keyset cursor returned by the previous page.

    Returns:
        Rows of ``{uid, lang, pos, lemma, uses}`` ordered by use count then lemma.
    """
    cursor = decode_cursor(after, "lexemes")
    params: dict[str, Any] = {
        "q": q or "",
        "qpat": f"%{q}%" if q else "",
        "pos": pos or "",
        "lang": lang or "",
        "limit": limit,
        "after_uses": cursor["uses"] if cursor else None,
        "after_lemma": cursor["lemma"] if cursor else None,
        "after_uid": cursor["uid"] if cursor else None,
    }
    if document_id is None:
        # Corpus-wide "busiest lexemes": read the precomputed, RLS-tenant-scoped
        # lexeme_stats rollup (migration 098) instead of GROUP BY-counting the
        # whole 17.5M-row lexeme_occurrences table on every page (D-024). uses is
        # per-tenant and the (tenant_id, uses DESC) index serves the ORDER BY.
        sql = """
        SELECT l.uid, l.lang, l.pos, l.lemma, ls.uses
        FROM lexeme_stats ls
        JOIN lexemes l ON l.uid = ls.lexeme_uid
        WHERE (%(q)s = '' OR l.lemma ILIKE %(qpat)s)
          AND (%(pos)s = '' OR l.pos = %(pos)s)
          AND (%(lang)s = '' OR l.lang = %(lang)s)
          AND (%(after_uses)s::bigint IS NULL OR ls.uses < %(after_uses)s::bigint
               OR (ls.uses = %(after_uses)s::bigint AND l.lemma > %(after_lemma)s)
               OR (ls.uses = %(after_uses)s::bigint AND l.lemma = %(after_lemma)s
                   AND l.uid > %(after_uid)s))
        ORDER BY ls.uses DESC, l.lemma, l.uid
        LIMIT %(limit)s
        """
    else:
        # Document-scoped: uses counts occurrences in ONE document only, which is
        # bounded to that document (RLS still decides its visibility). No rollup —
        # a per-document lexeme-usage rollup is not maintained.
        params["doc"] = document_id
        sql = """
        WITH ranked AS (
        SELECT l.uid, l.lang, l.pos, l.lemma, count(o.lexeme_uid) AS uses
        FROM lexemes l
        JOIN lexeme_occurrences o ON o.lexeme_uid = l.uid
        JOIN annotations ann ON ann.id = o.annotation_id AND ann.document_id = %(doc)s::uuid
        WHERE (%(q)s = '' OR l.lemma ILIKE %(qpat)s)
          AND (%(pos)s = '' OR l.pos = %(pos)s)
          AND (%(lang)s = '' OR l.lang = %(lang)s)
        GROUP BY l.uid, l.lang, l.pos, l.lemma
        )
        SELECT * FROM ranked
        WHERE (%(after_uses)s::bigint IS NULL OR uses < %(after_uses)s::bigint
               OR (uses = %(after_uses)s::bigint AND lemma > %(after_lemma)s)
               OR (uses = %(after_uses)s::bigint AND lemma = %(after_lemma)s
                   AND uid > %(after_uid)s))
        ORDER BY uses DESC, lemma, uid
        LIMIT %(limit)s
        """
    rows = await _rows(conn, sql, params)
    for row in rows:
        row["cursor"] = encode_cursor(
            "lexemes", uses=int(row["uses"]), lemma=row["lemma"], uid=row["uid"]
        )
    return rows


async def lexeme_kwic(
    conn: _AConn,
    uid: str,
    *,
    document_id: str | None = None,
    limit: int = 80,
    after: str | None = None,
) -> list[Row]:
    """Return keyword-in-context occurrences for a lexeme.

    Each row carries the ±45-character ``kwic`` window plus a ``pre``/``hit``/``post``
    split around the surface form for highlighting.

    Args:
        conn: Async database connection.
        uid: Lexeme uid.
        document_id: When set, only occurrences in this document.
        limit: Maximum occurrences to return.
        after: Optional keyset cursor returned by the previous page.

    Returns:
        Rows of ``{doc_id, title, char_start, surface, kwic, pre, hit, post}``.
    """
    cursor = decode_cursor(after, "lexeme-occurrences")
    rows = await _rows(
        conn,
        """
        SELECT d.id AS doc_id, d.title, s.char_start, o.annotation_id, o.span_id,
               substring(c.full_text FROM greatest(s.char_start - 45, 0) + 1
                         FOR (s.char_end - s.char_start) + 90) AS kwic,
               substring(c.full_text FROM s.char_start + 1
                         FOR s.char_end - s.char_start) AS surface
        FROM lexeme_occurrences o
        JOIN annotations a      ON a.id = o.annotation_id
        JOIN spans s            ON s.id = o.span_id
        JOIN documents d        ON d.id = a.document_id
        JOIN document_content c ON c.document_id = d.id
        WHERE o.lexeme_uid = %(uid)s
          AND (%(doc)s::uuid IS NULL OR a.document_id = %(doc)s::uuid)
          AND (%(after_title)s::text IS NULL OR d.title > %(after_title)s
               OR (d.title = %(after_title)s AND s.char_start > %(after_start)s::int)
               OR (d.title = %(after_title)s AND s.char_start = %(after_start)s::int
                   AND o.annotation_id > %(after_annotation)s::ulid)
               OR (d.title = %(after_title)s AND s.char_start = %(after_start)s::int
                   AND o.annotation_id = %(after_annotation)s::ulid
                   AND o.span_id > %(after_span)s::uuid))
        ORDER BY d.title, s.char_start, o.annotation_id, o.span_id
        LIMIT %(limit)s
        """,
        {
            "uid": uid,
            "doc": document_id,
            "limit": limit,
            "after_title": cursor["title"] if cursor else None,
            "after_start": cursor["char_start"] if cursor else None,
            "after_annotation": cursor["annotation_id"] if cursor else None,
            "after_span": cursor["span_id"] if cursor else None,
        },
    )
    for r in rows:
        r["pre"], r["hit"], r["post"] = _split_kwic(r["kwic"] or "", r["surface"] or "")
        r["cursor"] = encode_cursor(
            "lexeme-occurrences",
            title=r["title"],
            char_start=r["char_start"],
            annotation_id=r["annotation_id"],
            span_id=r["span_id"],
        )
    return rows


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------


async def search_sentences(
    conn: _AConn,
    term: str,
    *,
    document_id: str | None = None,
    limit: int = 100,
    after: str | None = None,
) -> list[Row]:
    """Return sentences containing *term* (case-insensitive literal substring).

    "Literal" is enforced, not assumed: ``%`` and ``_`` are data rather than
    wildcards, and the term is matched across typographic confusables (``'`` also
    finds ``’``, ``-`` also finds ``—``), because a corpus extracted from PDF/EPUB
    stores the typographic forms while callers type the ASCII ones. The rule and
    its bounds live in :mod:`doc_ray.domain.search_terms`.

    Args:
        conn: Async database connection.
        term: Substring to match against sentence text.
        document_id: When set, only sentences of this document.
        limit: Maximum sentences to return.
        after: Optional keyset cursor returned by the previous page.

    Returns:
        Rows of ``{id, doc_id, title, ordinal, text, pre, hit, post}`` — ``id`` is
        the sentence ULID, so hits are citable without a second round-trip.
    """
    cursor = decode_cursor(after, "sentence-search")
    match_sql, match_params = match_clause("s.text", term)
    rows = await _rows(
        conn,
        "SELECT s.id, d.id AS doc_id, d.title, s.ordinal, s.text "
        "FROM sentences s JOIN documents d ON d.id = s.document_id "
        f"WHERE {match_sql} "
        "  AND (%(doc)s::uuid IS NULL OR s.document_id = %(doc)s::uuid) "
        "  AND (%(after_title)s::text IS NULL OR d.title > %(after_title)s "
        "       OR (d.title = %(after_title)s AND s.ordinal > %(after_ord)s::int) "
        "       OR (d.title = %(after_title)s AND s.ordinal = %(after_ord)s::int "
        "           AND s.id > %(after_id)s::ulid)) "
        "ORDER BY d.title, s.ordinal, s.id LIMIT %(limit)s",
        {
            **match_params,
            "doc": document_id,
            "limit": limit,
            "after_title": cursor["title"] if cursor else None,
            "after_ord": cursor["ordinal"] if cursor else None,
            "after_id": cursor["id"] if cursor else None,
        },
    )
    for r in rows:
        r["pre"], r["hit"], r["post"] = _split_kwic(r["text"], term)
        r["cursor"] = encode_cursor(
            "sentence-search", title=r["title"], ordinal=r["ordinal"], id=r["id"]
        )
    return rows


# --------------------------------------------------------------------------
# Named entities (NER layer)
# --------------------------------------------------------------------------

_ENTITY_FROM = """
    FROM entity_mention em
    JOIN annotations a      ON a.id = em.annotation_id
    JOIN spans sp           ON sp.id = em.span_id
    JOIN documents d        ON d.id = a.document_id
    WHERE (%(doc)s::uuid IS NULL OR a.document_id = %(doc)s::uuid)
      AND (%(etype)s = '' OR em.entity_type = %(etype)s)
"""


async def list_entities(
    conn: _AConn,
    *,
    document_id: str | None = None,
    entity_type: str | None = None,
    limit: int = 100,
    after: str | None = None,
) -> list[Row]:
    """Return named-entity mentions with their surface text and span offsets.

    Args:
        conn: Async database connection.
        document_id: When set, only mentions in this document.
        entity_type: Exact type filter (``PERSON`` / ``ORG`` / ``GPE`` / ...), or ``None``.
        limit: Maximum mentions to return.
        after: Optional keyset cursor returned by the previous page.

    Returns:
        Rows of ``{id, doc_id, title, entity_type, text, char_start, char_end}``
        in document order.
    """
    cursor = decode_cursor(after, "entities")
    rows = await _rows(
        conn,
        "SELECT em.id, a.document_id AS doc_id, d.title, em.entity_type, "
        "       sp.char_start, sp.char_end, em.surface AS text "
        + _ENTITY_FROM
        + "AND (%(after_title)s::text IS NULL OR d.title > %(after_title)s "
        + "     OR (d.title = %(after_title)s AND sp.char_start > %(after_start)s::int) "
        + "     OR (d.title = %(after_title)s AND sp.char_start = %(after_start)s::int "
        + "         AND em.id > %(after_id)s::ulid)) "
        + "ORDER BY d.title, sp.char_start, em.id LIMIT %(limit)s",
        {
            "doc": document_id,
            "etype": entity_type or "",
            "limit": limit,
            "after_title": cursor["title"] if cursor else None,
            "after_start": cursor["char_start"] if cursor else None,
            "after_id": cursor["id"] if cursor else None,
        },
    )
    for row in rows:
        row["cursor"] = encode_cursor(
            "entities", title=row["title"], char_start=row["char_start"], id=row["id"]
        )
    return rows


async def entity_frequency(
    conn: _AConn,
    *,
    document_id: str | None = None,
    entity_type: str | None = None,
    limit: int = 100,
    after: str | None = None,
) -> list[Row]:
    """Return entity mention frequencies (character/place/org tables).

    Groups mentions by surface text and type, most frequent first — the
    document-scoped "who appears in this novel" query.

    Args:
        conn: Async database connection.
        document_id: When set, count within this document only.
        entity_type: Exact type filter, or ``None`` for all types.
        limit: Maximum distinct (text, type) rows to return.
        after: Optional keyset cursor returned by the previous page.

    Returns:
        Rows of ``{text, entity_type, count}`` ordered by descending count.
    """
    cursor = decode_cursor(after, "entity-frequency")
    params = {
        "doc": document_id,
        "etype": entity_type or "",
        "limit": limit,
        "after_count": cursor["count"] if cursor else None,
        "after_text": cursor["text"] if cursor else None,
        "after_type": cursor["entity_type"] if cursor else None,
    }
    if document_id is None:
        # Corpus-wide frequency: read the precomputed, RLS-tenant-scoped
        # entity_frequency_stats rollup (migration 100) instead of GROUP BY-counting
        # the 1.1M-row entity_mention table live (D-024). The (tenant_id, uses DESC)
        # index serves the ORDER BY.
        sql = (
            "SELECT entity_text AS text, entity_type, uses AS count "
            "FROM entity_frequency_stats "
            "WHERE (%(etype)s = '' OR entity_type = %(etype)s) "
            "  AND (%(after_count)s::bigint IS NULL OR uses < %(after_count)s::bigint "
            "       OR (uses = %(after_count)s::bigint AND entity_text > %(after_text)s) "
            "       OR (uses = %(after_count)s::bigint AND entity_text = %(after_text)s "
            "           AND entity_type > %(after_type)s)) "
            "ORDER BY uses DESC, entity_text, entity_type LIMIT %(limit)s"
        )
    else:
        # Document-scoped: bounded to one document (RLS decides visibility).
        sql = (
            "WITH ranked AS ("
            "SELECT em.surface AS text, em.entity_type, count(*) AS count "
            + _ENTITY_FROM
            + "GROUP BY text, em.entity_type) "
            + "SELECT * FROM ranked "
            + "WHERE (%(after_count)s::bigint IS NULL OR count < %(after_count)s::bigint "
            + "       OR (count = %(after_count)s::bigint AND text > %(after_text)s) "
            + "       OR (count = %(after_count)s::bigint AND text = %(after_text)s "
            + "           AND entity_type > %(after_type)s)) "
            + "ORDER BY count DESC, text, entity_type LIMIT %(limit)s"
        )
    rows = await _rows(conn, sql, params)
    for row in rows:
        row["cursor"] = encode_cursor(
            "entity-frequency",
            count=int(row["count"]),
            text=row["text"],
            entity_type=row["entity_type"],
        )
    return rows


async def list_document_shares(conn: _AConn, document_id: str) -> list[Row]:
    """Return the ``document_acl`` grants on *document_id* the caller may see.

    RLS scopes the rows (migration 108): a sharer sees the grants they manage, a
    grantee sees their own — so a principal with neither returns an empty list.
    The grantee's email is resolved through the tenant-scoped SECURITY DEFINER
    helper, never a join to ``principal_credentials`` (which the read role cannot
    select and which holds password hashes).
    """
    return await _rows(
        conn,
        "SELECT principal_id, permission, granted_at, "
        "       docray_email_for_principal(principal_id) AS email "
        "FROM document_acl WHERE document_id = %s "
        "ORDER BY granted_at, permission",
        (document_id,),
    )
