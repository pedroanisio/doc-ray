"""Async read adapter for allusion edges (issue #9).

Serves the GraphQL ``allusions`` read, traversable in both directions. Every read is
bounded, keyset-paginated, and joins only ``entity_allusion`` to ``canonical`` /
``document_collections`` for display names — never a live aggregate over a fact table
(D-024). RLS on ``entity_allusion`` (migration 131) scopes every row to edges whose
BOTH lenses and evidence document the caller may see; this adapter adds no
authorization of its own.
"""

from __future__ import annotations

from typing import Any

import psycopg

from doc_ray.adapters.read_cursors import decode_cursor, encode_cursor
from doc_ray.domain.allusions import Direction

Row = dict[str, Any]
_AConn = psycopg.AsyncConnection[Row]

_COLS_AND_JOINS = """
    SELECT ea.id, ea.edge_type, ea.confidence, ea.method, ea.status,
           ea.reviewer, ea.rationale, ea.evidence_document_id, ea.created_at,
           ea.from_canonical_id, ea.from_lens_id, ea.to_canonical_id, ea.to_lens_id,
           cf.preferred AS from_preferred, cf.wikidata_id AS from_wikidata_id,
           ct.preferred AS to_preferred, ct.wikidata_id AS to_wikidata_id,
           lf.name AS from_lens_name, lt.name AS to_lens_name
    FROM entity_allusion ea
    JOIN canonical cf ON cf.id = ea.from_canonical_id
    JOIN canonical ct ON ct.id = ea.to_canonical_id
    JOIN document_collections lf ON lf.id = ea.from_lens_id
    JOIN document_collections lt ON lt.id = ea.to_lens_id
"""

#: The anchor clause per direction. ``outgoing`` = edges FROM the anchor entity;
#: ``incoming`` = edges TO it; ``both`` = either endpoint. Fixed fragments (never user
#: text), chosen by the validated Direction enum.
_WHERE_BY_DIRECTION = {
    Direction.OUTGOING: "(ea.from_canonical_id = %(cid)s AND ea.from_lens_id = %(lens)s::uuid)",
    Direction.INCOMING: "(ea.to_canonical_id = %(cid)s AND ea.to_lens_id = %(lens)s::uuid)",
    Direction.BOTH: (
        "((ea.from_canonical_id = %(cid)s AND ea.from_lens_id = %(lens)s::uuid) "
        "OR (ea.to_canonical_id = %(cid)s AND ea.to_lens_id = %(lens)s::uuid))"
    ),
}

_LIST_TAIL = """
      AND (%(after_ts)s::timestamptz IS NULL
           OR ea.created_at < %(after_ts)s::timestamptz
           OR (ea.created_at = %(after_ts)s::timestamptz AND ea.id < %(after_id)s::uuid))
    ORDER BY ea.created_at DESC, ea.id DESC
    LIMIT %(limit)s
"""


async def list_allusions(
    conn: _AConn,
    *,
    canonical_id: int,
    lens_id: str,
    direction: Direction,
    limit: int = 50,
    after: str | None = None,
) -> list[Row]:
    """Return allusion edges anchored on ``(canonical_id, lens_id)`` in *direction*.

    Newest first, keyset-paginated. An edge appears only if the caller may see both of
    its lenses and its evidence document (RLS) — a caller who can read only one side
    sees nothing (the block's load-bearing guarantee).
    """
    cursor = decode_cursor(after, "allusions")
    sql = f"{_COLS_AND_JOINS} WHERE {_WHERE_BY_DIRECTION[direction]} {_LIST_TAIL}"
    cur = await conn.execute(
        sql,
        {
            "cid": canonical_id,
            "lens": lens_id,
            "after_ts": cursor["created_at"] if cursor else None,
            "after_id": cursor["id"] if cursor else None,
            "limit": limit,
        },
    )
    rows = await cur.fetchall()
    for row in rows:
        row["cursor"] = encode_cursor("allusions", created_at=row["created_at"], id=row["id"])
    return rows


async def get_allusion(conn: _AConn, allusion_id: str) -> Row | None:
    """Return one edge by id (RLS-scoped), or ``None`` — used by the read query."""
    cur = await conn.execute(f"{_COLS_AND_JOINS} WHERE ea.id = %(id)s::uuid", {"id": allusion_id})
    row = await cur.fetchone()
    if row is not None:
        row["cursor"] = encode_cursor("allusions", created_at=row["created_at"], id=row["id"])
    return row


async def hydrate_names(conn: _AConn, edge: Row) -> Row:
    """Enrich a raw ``entity_allusion`` row with endpoint display names.

    Used to return a just-created/updated edge (from a write's ``RETURNING``) without a
    SELECT-policy round-trip — the proposer of a cross-lens edge may not be able to read
    it back. ``canonical`` and ``document_collections`` are shared, readable metadata
    (no document-scoped RLS), so the names never leak beyond what the registry exposes.
    """
    cur = await conn.execute(
        """
        SELECT cf.preferred AS from_preferred, cf.wikidata_id AS from_wikidata_id,
               ct.preferred AS to_preferred, ct.wikidata_id AS to_wikidata_id,
               lf.name AS from_lens_name, lt.name AS to_lens_name
        FROM (SELECT %(fc)s::bigint fc, %(fl)s::uuid fl, %(tc)s::bigint tc, %(tl)s::uuid tl) k
        JOIN canonical cf ON cf.id = k.fc
        JOIN canonical ct ON ct.id = k.tc
        JOIN document_collections lf ON lf.id = k.fl
        JOIN document_collections lt ON lt.id = k.tl
        """,
        {
            "fc": edge["from_canonical_id"],
            "fl": edge["from_lens_id"],
            "tc": edge["to_canonical_id"],
            "tl": edge["to_lens_id"],
        },
    )
    names = await cur.fetchone()
    enriched = dict(edge, **(names or {}))
    enriched["cursor"] = encode_cursor("allusions", created_at=edge["created_at"], id=edge["id"])
    return enriched
