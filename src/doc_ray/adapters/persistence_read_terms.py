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
