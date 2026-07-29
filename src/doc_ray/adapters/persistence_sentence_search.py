"""Bounded candidate-and-verify sentence phrase search (#45).

SQL finds an indexed superset; ``surface_word_v1`` alone decides which rows are
hits and where their exact citations begin and end. The hard candidate cap keeps
request work bounded independently of the caller's page size. Scan metadata rides
the list so GraphQL/MCP can distinguish an exhaustive verified zero from a capped
zero without changing the historical substring row shape.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any, Literal

import psycopg

from doc_ray.adapters.read_cursors import decode_cursor, encode_cursor
from doc_ray.adapters.sentence_search_sql import (
    candidate_probe,
    match_clause,
    phrase_candidate_clause,
)
from doc_ray.domain.phrase_profile import find_occurrences
from doc_ray.domain.search_terms import kwic

Row = dict[str, Any]
_AConn = psycopg.AsyncConnection[Row]
SentenceMatch = Literal["substring", "phrase"]

# Sibling of term_frequency.SENTENCE_HIGH_HITS (also 2001), intentionally
# independent: that cap bounds a count; this one bounds rows verified per search.
PHRASE_CANDIDATE_SCAN_CAP = 2001
PHRASE_CANDIDATE_BATCH_SIZE = 200

_logger = logging.getLogger("doc_ray.retrieval")


class SentenceSearchRows(list[Row]):
    """Search rows plus bounded-scan evidence used by result envelopes."""

    def __init__(
        self,
        rows: Iterable[Row] = (),
        *,
        candidate_scanned: int = 0,
        candidate_limit: int = PHRASE_CANDIDATE_SCAN_CAP,
        candidate_limit_reached: bool = False,
        verified: int = 0,
    ) -> None:
        """Attach candidate-scan evidence to an otherwise ordinary row list."""
        super().__init__(rows)
        self.candidate_scanned = candidate_scanned
        self.candidate_limit = candidate_limit
        self.candidate_limit_reached = candidate_limit_reached
        self.verified = verified


async def _rows(conn: _AConn, sql: str, params: dict[str, Any]) -> list[Row]:
    cur = await conn.execute(sql, params)
    return await cur.fetchall()


async def complete_candidate_ids(
    conn: _AConn, term: str, document_id: str | None, *, cap: int
) -> list[str] | None:
    """Every sentence id the index can offer for *term*, or ``None``.

    ``None`` is the instruction "page the way you always did". It is returned
    for three different reasons, deliberately collapsed into one answer,
    because in each case there is no complete candidate set to order and the
    pre-existing predicate is still correct:

    * the term cannot drive the index at all;
    * the helper truncated, so its subset is arbitrary;
    * the helper found nothing.

    That last case is the one worth stating. An empty result is *ambiguous*:
    it means "no sentence matches" or it means "this connection resolves no
    readable documents", and those are indistinguishable from here. The helper
    is ``SECURITY DEFINER`` and applies ``docray_readable_documents()``
    explicitly instead of leaning on the policy, so a connection that is exempt
    from RLS — an owner or ``BYPASSRLS`` role, which sees every row under the
    old predicate — resolves *no* principal and therefore no readable
    documents. Restricting such a caller to the empty set would turn "you may
    read everything" into "you may read nothing", silently and only for
    indexable terms.

    Falling back instead costs a wasted probe on a genuine miss, which is the
    cheap case anyway: a query that matches nothing is not the one D-035
    measured at 3,774 ms. It buys back the property that routing can only ever
    narrow a set the caller was already going to be shown.
    """
    probe = candidate_probe(term, document_id, cap=cap)
    if probe is None:
        return None
    sql, params = probe
    rows = await _rows(conn, sql, params)
    if not rows or len(rows) > cap:
        return None
    return [str(row["id"]) for row in rows]


def _id_restriction(candidate_ids: list[str] | None) -> tuple[str, dict[str, Any]]:
    """Narrow to a complete candidate set, or add nothing to the query."""
    if candidate_ids is None:
        return "", {}
    return " AND s.id = ANY(%(cand_ids)s::ulid[])", {"cand_ids": candidate_ids}


def verify_candidate(row: Row, phrase: str) -> Row | None:
    """Shape one candidate as a phrase hit, or reject it."""
    source_text = str(row.get("citation_text") or row["text"])
    local = find_occurrences(source_text, phrase)
    if not local:
        return None
    shaped = dict(row)
    sentence_start = int(shaped.pop("char_start"))
    shaped.pop("citation_text", None)
    shaped["occurrences"] = [
        {"char_start": sentence_start + start, "char_end": sentence_start + end}
        for start, end in local
    ]
    shaped["occurrence_count"] = len(local)
    first_start, first_end = local[0]
    shaped["pre"] = source_text[:first_start]
    shaped["hit"] = source_text[first_start:first_end]
    shaped["post"] = source_text[first_end:]
    shaped["cursor"] = encode_cursor(
        "sentence-search",
        title=shaped["title"],
        ordinal=shaped["ordinal"],
        id=shaped["id"],
    )
    return shaped


async def search_phrase_sentences(
    conn: _AConn,
    phrase: str,
    *,
    document_id: str | None = None,
    limit: int = 100,
    after: str | None = None,
) -> SentenceSearchRows:
    """Return a keyset-ordered page of verified phrase hits."""
    cursor = decode_cursor(after, "sentence-search")
    match_sql, match_params = phrase_candidate_clause("s.text", phrase)
    emitted: list[Row] = []
    scanned = 0
    verified = 0
    candidate_limit = PHRASE_CANDIDATE_SCAN_CAP
    # Probed once for the whole search, not per batch: the candidate set is a
    # property of the phrase, and re-probing between batches could shift it
    # under the resumable cursor.
    restrict_sql, restrict_params = _id_restriction(
        await complete_candidate_ids(conn, phrase, document_id, cap=candidate_limit)
    )

    while scanned < candidate_limit and len(emitted) < limit:
        batch_limit = min(PHRASE_CANDIDATE_BATCH_SIZE, candidate_limit - scanned)
        candidates = await _rows(
            conn,
            "SELECT s.id, d.id AS doc_id, d.title, s.ordinal, s.text, sp.char_start, "
            "substring(c.full_text FROM sp.char_start + 1 "
            "          FOR sp.char_end - sp.char_start) AS citation_text "
            "FROM sentences s JOIN documents d ON d.id = s.document_id "
            "JOIN spans sp ON sp.id = s.span_id "
            "JOIN document_content c ON c.document_id = s.document_id "
            f"WHERE {match_sql}{restrict_sql} "
            "  AND (%(doc)s::uuid IS NULL OR s.document_id = %(doc)s::uuid) "
            "  AND (%(after_title)s::text IS NULL OR d.title > %(after_title)s "
            "       OR (d.title = %(after_title)s AND s.ordinal > %(after_ord)s::int) "
            "       OR (d.title = %(after_title)s AND s.ordinal = %(after_ord)s::int "
            "           AND s.id > %(after_id)s::ulid)) "
            "ORDER BY d.title, s.ordinal, s.id LIMIT %(candidate_limit)s",
            {
                **match_params,
                **restrict_params,
                "doc": document_id,
                "candidate_limit": batch_limit,
                "after_title": cursor["title"] if cursor else None,
                "after_ord": cursor["ordinal"] if cursor else None,
                "after_id": cursor["id"] if cursor else None,
            },
        )
        if not candidates:
            break
        for candidate in candidates:
            scanned += 1
            cursor = {
                "title": candidate["title"],
                "ordinal": candidate["ordinal"],
                "id": candidate["id"],
            }
            hit = verify_candidate(candidate, phrase)
            if hit is not None:
                verified += 1
                emitted.append(hit)
                if len(emitted) >= limit:
                    break
        if len(emitted) >= limit:
            break
        if len(candidates) < batch_limit:
            break

    cap_reached = scanned >= candidate_limit and len(emitted) < limit
    _logger.debug(
        "phrase sentence search candidate_scanned=%d verified=%d emitted=%d cap_reached=%s",
        scanned,
        verified,
        len(emitted),
        cap_reached,
    )
    return SentenceSearchRows(
        emitted,
        candidate_scanned=scanned,
        candidate_limit=candidate_limit,
        candidate_limit_reached=cap_reached,
        verified=verified,
    )


async def search_sentences(
    conn: _AConn,
    term: str,
    *,
    document_id: str | None = None,
    limit: int = 100,
    after: str | None = None,
    match: SentenceMatch = "substring",
) -> list[Row]:
    """Return historical substring hits or bounded, verified phrase hits."""
    if match == "phrase":
        return await search_phrase_sentences(
            conn, term, document_id=document_id, limit=limit, after=after
        )
    if match != "substring":
        raise ValueError("match must be 'substring' or 'phrase'")

    # Frozen compatibility path: query, ordering, cursor, and KWIC are the exact
    # historical implementation. Phrase-only joins/fields never reach it. The id
    # restriction narrows which rows are considered; it changes neither the
    # predicate that decides a hit nor the order they come back in.
    cursor = decode_cursor(after, "sentence-search")
    match_sql, match_params = match_clause("s.text", term)
    restrict_sql, restrict_params = _id_restriction(
        await complete_candidate_ids(conn, term, document_id, cap=PHRASE_CANDIDATE_SCAN_CAP)
    )
    rows = await _rows(
        conn,
        "SELECT s.id, d.id AS doc_id, d.title, s.ordinal, s.text "
        "FROM sentences s JOIN documents d ON d.id = s.document_id "
        f"WHERE {match_sql}{restrict_sql} "
        "  AND (%(doc)s::uuid IS NULL OR s.document_id = %(doc)s::uuid) "
        "  AND (%(after_title)s::text IS NULL OR d.title > %(after_title)s "
        "       OR (d.title = %(after_title)s AND s.ordinal > %(after_ord)s::int) "
        "       OR (d.title = %(after_title)s AND s.ordinal = %(after_ord)s::int "
        "           AND s.id > %(after_id)s::ulid)) "
        "ORDER BY d.title, s.ordinal, s.id LIMIT %(limit)s",
        {
            **match_params,
            **restrict_params,
            "doc": document_id,
            "limit": limit,
            "after_title": cursor["title"] if cursor else None,
            "after_ord": cursor["ordinal"] if cursor else None,
            "after_id": cursor["id"] if cursor else None,
        },
    )
    for row in rows:
        row["pre"], row["hit"], row["post"] = kwic(row["text"], term)
        row["cursor"] = encode_cursor(
            "sentence-search", title=row["title"], ordinal=row["ordinal"], id=row["id"]
        )
    return rows
