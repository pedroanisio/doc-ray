"""Async read adapter for the hyper-context lens rollups (issue #10, FR-5).

Serves ``lensEntities`` (ranked entities), ``entityRelations`` (direct co-occurrence
relations), and ``relatedEntities`` (bounded N-hop discovery). Every read is served from
a precomputed rollup (``lens_entity_rollup`` / ``lens_entity_relation``, migration 133),
keyset-paginated and bounded — never a live aggregate over a fact table (D-024). RLS on
those tables scopes every row to lenses the caller may see; this adapter adds no
authorization of its own. Every row carries ``refreshed_at`` so a consumer sees staleness.
"""

from __future__ import annotations

from typing import Any

import psycopg

from doc_ray.adapters.read_cursors import decode_cursor, encode_cursor
from doc_ray.domain.lens_rollups import build_adjacency, related_within_hops

Row = dict[str, Any]
_AConn = psycopg.AsyncConnection[Row]


async def _rows(conn: _AConn, sql: str, params: dict[str, Any]) -> list[Row]:
    cur = await conn.execute(sql, params)
    return await cur.fetchall()


_LENS_ENTITIES_SQL = """
    SELECT r.canonical_id, r.scope, r.mention_count, r.document_count, r.type_profile,
           r.type_consistency, r.first_seen_ordinal, r.refreshed_at,
           c.preferred, c.entity_type, c.wikidata_id
    FROM lens_entity_rollup r
    JOIN canonical c ON c.id = r.canonical_id
    WHERE r.lens_id = %(lens)s::uuid
      AND r.mention_count >= %(min_mentions)s
      AND (%(after_n)s::bigint IS NULL
           OR r.mention_count < %(after_n)s::bigint
           OR (r.mention_count = %(after_n)s::bigint AND r.canonical_id > %(after_id)s::bigint))
    ORDER BY r.mention_count DESC, r.canonical_id
    LIMIT %(limit)s
"""


async def lens_entities(
    conn: _AConn,
    lens_id: str,
    *,
    limit: int = 100,
    after: str | None = None,
    min_mentions: int = 0,
) -> list[Row]:
    """Return a lens's significant entities, most-mentioned first (hyper-context rollup)."""
    cursor = decode_cursor(after, "lens-entities")
    rows = await _rows(
        conn,
        _LENS_ENTITIES_SQL,
        {
            "lens": lens_id,
            "min_mentions": min_mentions,
            "after_n": cursor["mention_count"] if cursor else None,
            "after_id": cursor["canonical_id"] if cursor else None,
            "limit": limit,
        },
    )
    for row in rows:
        row["cursor"] = encode_cursor(
            "lens-entities", mention_count=row["mention_count"], canonical_id=row["canonical_id"]
        )
    return rows


# The undirected pair is stored once (from < to), so an entity's relations are found by
# matching EITHER endpoint; the "other" endpoint is the related entity.
_RELATIONS_SQL = """
    SELECT r.cooccurrence_count, r.strength, r.refreshed_at,
           CASE WHEN r.from_canonical_id = %(cid)s THEN r.to_canonical_id
                ELSE r.from_canonical_id END AS other_canonical_id,
           c.preferred, c.entity_type, c.wikidata_id
    FROM lens_entity_relation r
    JOIN canonical c ON c.id = (CASE WHEN r.from_canonical_id = %(cid)s
                                     THEN r.to_canonical_id ELSE r.from_canonical_id END)
    WHERE r.lens_id = %(lens)s::uuid
      AND (r.from_canonical_id = %(cid)s OR r.to_canonical_id = %(cid)s)
      AND r.strength >= %(min_strength)s
      AND (%(after_s)s::real IS NULL
           OR r.strength < %(after_s)s::real
           OR (r.strength = %(after_s)s::real
               AND (CASE WHEN r.from_canonical_id = %(cid)s THEN r.to_canonical_id
                         ELSE r.from_canonical_id END) > %(after_id)s::bigint))
    ORDER BY r.strength DESC, other_canonical_id
    LIMIT %(limit)s
"""


async def entity_relations(
    conn: _AConn,
    lens_id: str,
    canonical_id: int,
    *,
    limit: int = 50,
    min_strength: float = 0.0,
    after: str | None = None,
) -> list[Row]:
    """Return an entity's direct co-occurrence relations in a lens, strongest first."""
    cursor = decode_cursor(after, "entity-relations")
    rows = await _rows(
        conn,
        _RELATIONS_SQL,
        {
            "lens": lens_id,
            "cid": canonical_id,
            "min_strength": min_strength,
            "after_s": cursor["strength"] if cursor else None,
            "after_id": cursor["canonical_id"] if cursor else None,
            "limit": limit,
        },
    )
    for row in rows:
        row["cursor"] = encode_cursor(
            "entity-relations", strength=row["strength"], canonical_id=row["other_canonical_id"]
        )
    return rows


async def related_entities(
    conn: _AConn, lens_id: str, canonical_id: int, *, hops: int, limit: int = 50
) -> list[Row]:
    """Return entities within *hops* of an anchor via co-occurrence (bounded N-hop).

    Loads the lens's relation edges (a bounded rollup, RLS-scoped), builds an in-memory
    adjacency map, and runs the pure :func:`related_within_hops` BFS — which enforces the
    hop cap (raising on an over-cap request) and terminates on cycles. Returns each
    reachable entity with its shortest ``hops`` distance and display name.
    """
    edge_rows = await _rows(
        conn,
        "SELECT from_canonical_id, to_canonical_id FROM lens_entity_relation "
        "WHERE lens_id = %(lens)s::uuid",
        {"lens": lens_id},
    )
    adjacency = build_adjacency(
        [(int(e["from_canonical_id"]), int(e["to_canonical_id"])) for e in edge_rows]
    )
    reached = related_within_hops(adjacency, canonical_id, max_hops=hops, limit=limit)
    if not reached:
        return []
    hop_by_id = {cid: h for cid, h in reached}
    names = await _rows(
        conn,
        "SELECT id AS canonical_id, preferred, entity_type, wikidata_id FROM canonical "
        "WHERE id = ANY(%(ids)s::bigint[])",
        {"ids": list(hop_by_id)},
    )
    out = [dict(n, hops=hop_by_id[int(n["canonical_id"])]) for n in names]
    out.sort(key=lambda r: (r["hops"], int(r["canonical_id"])))
    return out
