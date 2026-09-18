"""Read adapter for the term-frequency pre-flight (issue #16).

One indexed batch read against the D-024-compliant ``lexeme_stats`` rollup — never
the 17.5M-row ``lexeme_occurrences`` fact table (whose live aggregation was the
measured 37 s Lexicon regression migration 098 exists to prevent). Given a list of
normalized lexeme keys it returns, per key, the corpus-wide use count and the
scope's total, both RLS-scoped to the caller's tenant by the ``lexeme_stats``
policy (``tenant_id = docray_current_tenant()``).

Split from ``persistence_read_structure.py`` for D-015 headroom and registered in
``scripts/check_read_path_aggregates.py``'s ``_READ_PATHS``. Phrase scans fetch a
bounded candidate superset in one statement, then the pure domain verifies every
occurrence under ``surface_word_v1``. Verdict bands remain domain-owned.
"""

from __future__ import annotations

import logging
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from doc_ray.adapters.sentence_search_sql import (
    match_clause,
    padded_index_patterns,
    phrase_candidate_clause,
)
from doc_ray.domain.phrase_profile import phrase_words
from doc_ray.domain.term_frequency import SentenceScan, verify_sentence_candidates

_AConn = AsyncConnection[Any]
Row = dict[str, Any]
_logger = logging.getLogger("doc_ray.retrieval")

#: ONE statement for the whole batch — never N queries in a loop (the cost this
#: issue removes). ``unnest`` turns the key array into rows; the LEFT JOIN onto the
#: shared lexeme registry lets an absent key (or a lexeme this tenant never used)
#: sum to 0 — the unattested path, distinguishable from a lookup failure. Multiple
#: lexemes for one lemma (a NOUN and a VERB "run") are summed together, because a
#: search on the surface term would hit them all. The scalar subquery is the share
#: denominator, evaluated once. Touches only the bounded lexeme registry + the
#: lexeme_stats rollup; ``lexeme_occurrences`` is never read (D-024).
#: The share denominator is READ, never derived. Deriving it was
#: `SUM(uses) FROM lexeme_stats` on every call — a read-path fact-table
#: aggregate (D-024) over 444k rows, and forced RLS evaluates the policy per
#: row: 2.0 s as BYPASSRLS but 22.9 s on the real `docray_api` path, against a
#: 15 s statement_timeout. Every term_frequency call failed, single ordinary
#: words included (BL-204). Migration 152 maintains the scalar on corpus_stats,
#: exactly as migration 142 did for the sentence denominator.
_TERM_FREQ_SQL = """
SELECT k.key AS key,
       COALESCE(SUM(ls.uses), 0)::bigint AS uses,
       (SELECT COALESCE(SUM(total_lexeme_uses), 0)::bigint FROM corpus_stats) AS total_uses
FROM unnest(%(keys)s::text[]) AS k(key)
LEFT JOIN lexemes l ON l.lemma = k.key
LEFT JOIN lexeme_stats ls ON ls.lexeme_uid = l.uid
GROUP BY k.key
"""

#: The scan's denominator: one scalar from the per-tenant rollup (migration 142).
#: Never ``count(*)`` over ``sentences`` — that is the D-024 read-path scan. Rides
#: every row of the scan statement as an uncorrelated subquery, evaluated once,
#: exactly as ``total_uses`` does above — so the batch stays ONE statement.
_TOTAL_SENTENCES_SQL = "(SELECT COALESCE(SUM(n_sentences), 0)::bigint FROM corpus_stats)"


async def _rows(conn: _AConn, sql: str, params: dict[str, Any]) -> list[Row]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, params)
        return list(await cur.fetchall())


async def term_frequencies(conn: _AConn, keys: list[str]) -> tuple[dict[str, int], int]:
    """Return ``(counts, total_uses)`` for a batch of normalized lexeme keys.

    ``counts`` maps each key to its corpus-wide use count (0 if unattested).
    ``total_uses`` is the caller-tenant total lexeme uses — the share denominator.
    A single SQL statement regardless of batch size (proven in
    test_term_frequency_db.py).

    Args:
        conn: Async database connection (RLS decides tenant visibility).
        keys: Normalized lexeme keys (``norm_lemma`` output), already de-duplicated.

    Returns:
        The per-key counts and the scope's total lexeme uses.
    """
    if not keys:
        return {}, 0
    rows = await _rows(conn, _TERM_FREQ_SQL, {"keys": list(keys)})
    counts = {str(row["key"]): int(row["uses"]) for row in rows}
    total_uses = int(rows[0]["total_uses"]) if rows else 0
    return counts, total_uses


def _scan_sql(keys_to_text: dict[str, str]) -> tuple[str, dict[str, Any]]:
    """Build ONE statement fetching bounded candidate sentences for every term.

    Shape per term::

        SELECT %(kN)s AS key, b.id, b.text
        FROM (VALUES (1)) seed
        LEFT JOIN LATERAL (
          SELECT id, text FROM sentences s WHERE <candidate> LIMIT %(cap)s
        ) b ON TRUE

    The ``LIMIT`` inside the subquery is what bounds the work: Postgres stops
    reading once the cap is reached. The outer left lateral join preserves a row
    for a term with no candidates, keeping the entire batch in one statement.

    Multi-word terms reuse :func:`phrase_candidate_clause`; one-profile-word
    compounds reuse :func:`match_clause`. Both are parameterized index-backed
    supersets. The pure domain verifier removes their false positives.

    Args:
        keys_to_text: Result key -> raw term to generate and verify candidates for.

    Returns:
        ``(sql, params)`` for a single ``UNION ALL`` statement.
    """
    parts: list[str] = []
    params: dict[str, Any] = {}
    for index, (key, term) in enumerate(keys_to_text.items()):
        params[f"k{index}"] = key
        patterns = padded_index_patterns(term)
        if patterns is None:
            # No word can drive the trigram index (all shorter than three
            # characters, confusable, or fold-matched). Keep the original
            # predicate: it is no slower than it was, and the helper could only
            # widen the superset without narrowing anything.
            clause = phrase_candidate_clause if len(phrase_words(term)) > 1 else match_clause
            predicate, clause_params = clause("s.text", term, prefix=f"t{index}")
            params.update(clause_params)
            source = f"SELECT s.id, s.text FROM sentences s WHERE {predicate} LIMIT %(cap)s"
        else:
            # Index-backed candidates. RLS forbids a non-leakproof ILIKE ahead of
            # its security qual, so the trigram index is only reachable inside
            # the SECURITY DEFINER helper, which applies the caller's readable
            # documents explicitly (migration 148).
            for slot, pattern in enumerate(patterns):
                params[f"t{index}_p{slot}"] = pattern
            args = ", ".join(f"%(t{index}_p{slot})s" for slot in range(len(patterns)))
            source = (
                f"SELECT s.id, s.text FROM docray_readable_sentence_ids("
                f"{args}, NULL::uuid, %(cap)s) s"
            )
        parts.append(
            f"SELECT %(k{index})s AS key, b{index}.id AS sentence_id, "
            f"b{index}.text AS text, "
            f"{_TOTAL_SENTENCES_SQL} AS total_sentences "
            f"FROM (VALUES (1)) seed{index}(one) "
            f"LEFT JOIN LATERAL ({source}) b{index} ON TRUE"
        )
    return "\nUNION ALL\n".join(parts), params


async def sentence_hit_counts(
    conn: _AConn, keys_to_text: dict[str, str], *, cap: int
) -> tuple[dict[str, SentenceScan], int]:
    """Return bounded candidates and verified occurrence counts per key.

    The measured half of the term-frequency pre-flight (#39): for terms the lexeme
    index cannot hold, SQL fetches an index-backed candidate superset bounded so
    the work cannot become a corpus scan. Pure ``surface_word_v1`` verification
    then counts boundary/adjacency-valid, overlapping occurrences.

    ``cap`` is supplied by the caller from the domain's band constant — the
    adapter must not decide where "too many to matter" sits.

    The denominator is one scalar from the ``corpus_stats`` rollup (migration 142),
    never ``count(*)`` over ``sentences`` (D-024). RLS scopes both: this adds no
    tenant filter of its own, exactly like :func:`term_frequencies`.

    Args:
        conn: Async database connection (RLS decides tenant visibility).
        keys_to_text: Result key -> raw term text to match.
        cap: Maximum candidates to fetch per term; reaching it makes every count
            a floor and sets ``capped``.

    Returns:
        Per-key :class:`~doc_ray.domain.term_frequency.SentenceScan` values, and
        the caller-tenant total sentence count.
    """
    if not keys_to_text:
        return {}, 0
    sql, params = _scan_sql(keys_to_text)
    params["cap"] = int(cap)
    rows = await _rows(conn, sql, params)
    candidates: dict[str, list[str]] = {key: [] for key in keys_to_text}
    for row in rows:
        if row["sentence_id"] is not None:
            candidates[str(row["key"])].append(str(row["text"]))
    scans: dict[str, SentenceScan] = {}
    for key, term in keys_to_text.items():
        scan = verify_sentence_candidates(term, candidates[key], cap=int(cap))
        scans[key] = scan
        _logger.debug(
            "term_frequency verified key=%r candidate_fetched=%d "
            "occurrence_sentences=%d occurrences=%d capped=%s profile=%s",
            key,
            scan.hits,
            scan.occurrence_sentences,
            scan.occurrences,
            scan.capped,
            scan.profile,
        )
    total_sentences = int(rows[0]["total_sentences"]) if rows else 0
    return scans, total_sentences
