"""Read adapter for the term-frequency pre-flight (MCP Item 10 / #16).

One indexed batch read against the D-024-compliant ``lexeme_stats`` rollup — never
the 17.5M-row ``lexeme_occurrences`` fact table (whose live aggregation was the
measured 37 s Lexicon regression migration 098 exists to prevent). Given a list of
normalized lexeme keys it returns, per key, the corpus-wide use count and the
scope's total, both RLS-scoped to the caller's tenant by the ``lexeme_stats``
policy (``tenant_id = docray_current_tenant()``).

Split from ``persistence_read_structure.py`` for D-015 headroom and registered in
``scripts/check_read_path_aggregates.py``'s ``_READ_PATHS``. The verdict is decided
in :mod:`doc_ray.domain.term_frequency`; this module only fetches the numbers.
"""

from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from doc_ray.adapters.sentence_search_sql import match_clause
from doc_ray.domain.term_frequency import SentenceScan

_AConn = AsyncConnection[Any]
Row = dict[str, Any]

#: ONE statement for the whole batch — never N queries in a loop (the cost this
#: issue removes). ``unnest`` turns the key array into rows; the LEFT JOIN onto the
#: shared lexeme registry lets an absent key (or a lexeme this tenant never used)
#: sum to 0 — the unattested path, distinguishable from a lookup failure. Multiple
#: lexemes for one lemma (a NOUN and a VERB "run") are summed together, because a
#: search on the surface term would hit them all. The scalar subquery is the share
#: denominator, evaluated once. Touches only the bounded lexeme registry + the
#: lexeme_stats rollup; ``lexeme_occurrences`` is never read (D-024).
_TERM_FREQ_SQL = """
SELECT k.key AS key,
       COALESCE(SUM(ls.uses), 0)::bigint AS uses,
       (SELECT COALESCE(SUM(uses), 0)::bigint FROM lexeme_stats) AS total_uses
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
    """Build ONE statement counting matching sentences per term, each bounded by a cap.

    Shape per term::

        SELECT %(kN)s AS key, count(*) AS hits
        FROM (SELECT 1 FROM sentences s WHERE <match_clause> LIMIT %(cap)s) b

    The ``LIMIT`` inside the subquery is what bounds the work: Postgres stops
    reading once the cap is reached, so a term that floods the corpus costs the
    same as one that appears ``cap`` times. ``hits >= cap`` is therefore a floor,
    reported as ``capped``.

    The predicate comes from :func:`match_clause` and never from an inlined
    ``ILIKE``. That single-rule seam is the whole point: it is what makes this
    verdict predict ``search_sentences`` instead of merely resembling it, and it
    is why a typed ``'`` finds the typographic ``’`` the extractor stored.

    Args:
        keys_to_text: Result key -> the raw term to match as a literal substring.

    Returns:
        ``(sql, params)`` for a single ``UNION ALL`` statement.
    """
    parts: list[str] = []
    params: dict[str, Any] = {}
    for index, (key, term) in enumerate(keys_to_text.items()):
        predicate, clause_params = match_clause("s.text", term, prefix=f"t{index}")
        params.update(clause_params)
        params[f"k{index}"] = key
        parts.append(
            f"SELECT %(k{index})s AS key, count(*)::bigint AS hits, "
            f"{_TOTAL_SENTENCES_SQL} AS total_sentences "
            f"FROM (SELECT 1 FROM sentences s WHERE {predicate} LIMIT %(cap)s) b{index}"
        )
    return "\nUNION ALL\n".join(parts), params


async def sentence_hit_counts(
    conn: _AConn, keys_to_text: dict[str, str], *, cap: int
) -> tuple[dict[str, SentenceScan], int]:
    """Return ``(per-key bounded sentence hits, tenant total sentences)``.

    The measured half of the term-frequency pre-flight (#39): for terms the lexeme
    index cannot hold, this counts the sentences a literal-substring search would
    actually match, bounded so the count can never become a corpus scan.

    ``cap`` is supplied by the caller from the domain's band constant — the
    adapter must not decide where "too many to matter" sits.

    The denominator is one scalar from the ``corpus_stats`` rollup (migration 142),
    never ``count(*)`` over ``sentences`` (D-024). RLS scopes both: this adds no
    tenant filter of its own, exactly like :func:`term_frequencies`.

    Args:
        conn: Async database connection (RLS decides tenant visibility).
        keys_to_text: Result key -> raw term text to match.
        cap: Maximum hits to count per term; reaching it sets ``capped``.

    Returns:
        Per-key :class:`~doc_ray.domain.term_frequency.SentenceScan` values, and
        the caller-tenant total sentence count.
    """
    if not keys_to_text:
        return {}, 0
    sql, params = _scan_sql(keys_to_text)
    params["cap"] = int(cap)
    rows = await _rows(conn, sql, params)
    scans = {
        str(row["key"]): SentenceScan(hits=int(row["hits"]), capped=int(row["hits"]) >= int(cap))
        for row in rows
    }
    total_sentences = int(rows[0]["total_sentences"]) if rows else 0
    return scans, total_sentences
