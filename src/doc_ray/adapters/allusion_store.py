"""Persistence for allusion-edge curation and the optional heuristic proposer (Block E, #9).

Writes to ``entity_allusion`` (migration 131) — a **curation** table the API may write
directly (docray_client), unlike Block D's derived fact tables. Every write is a review
action; a machine-proposed edge lands ``status='proposed'`` and only a human/agent
confirmation promotes it (PALS's Law). Nothing here reads or mutates projection /
resolution / canonical identity: an allusion is additive metadata, never an equality.
"""

from __future__ import annotations

from typing import Any

import psycopg

from doc_ray.domain.allusions import STATUS_PROPOSED

_AConn = psycopg.AsyncConnection[dict[str, Any]]
_Conn = psycopg.Connection[dict[str, Any]]

_CONFLICT_KEY = "(from_canonical_id, from_lens_id, to_canonical_id, to_lens_id, edge_type)"


_RETURN_COLS = (
    "id, from_canonical_id, from_lens_id, to_canonical_id, to_lens_id, edge_type, "
    "confidence, method, status, reviewer, rationale, evidence_document_id, created_at"
)


async def propose_allusion(
    conn: _AConn,
    *,
    from_canonical_id: int,
    from_lens_id: str,
    to_canonical_id: int,
    to_lens_id: str,
    edge_type: str,
    method: str,
    confidence: float | None,
    rationale: str | None,
    evidence_document_id: str | None,
) -> tuple[dict[str, Any] | None, bool]:
    """Insert a proposed edge idempotently; return ``(row, created)``.

    ``ON CONFLICT DO NOTHING`` protects an existing edge (a duplicate proposal must not
    reset a ``confirmed`` edge back to ``proposed``). On create, the row is returned via
    ``RETURNING`` (which bypasses the SELECT policy — a proposer of a cross-lens edge may
    not be able to *read* the far lens); on conflict, the existing row is read back
    (RLS-scoped) and ``created=False``. Always lands ``status='proposed'``.
    """
    cur = await conn.execute(
        f"""
        INSERT INTO entity_allusion
            (from_canonical_id, from_lens_id, to_canonical_id, to_lens_id, edge_type,
             confidence, method, status, rationale, evidence_document_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT {_CONFLICT_KEY} DO NOTHING
        RETURNING {_RETURN_COLS}
        """,
        (
            from_canonical_id,
            from_lens_id,
            to_canonical_id,
            to_lens_id,
            edge_type,
            confidence,
            method,
            STATUS_PROPOSED,
            rationale,
            evidence_document_id,
        ),
    )
    row = await cur.fetchone()
    if row is not None:
        return row, True
    existing = await conn.execute(
        f"SELECT {_RETURN_COLS} FROM entity_allusion WHERE {_CONFLICT_KEY} = (%s, %s, %s, %s, %s)",
        (from_canonical_id, from_lens_id, to_canonical_id, to_lens_id, edge_type),
    )
    return await existing.fetchone(), False


async def set_allusion_status(
    conn: _AConn, allusion_id: str, *, status: str, reviewer: str | None
) -> dict[str, Any] | None:
    """Confirm/reject an edge (records the reviewer); return the row via ``RETURNING``.

    ``None`` means the edge does not exist *or* the caller may not write it (RLS): the
    caller must be able to write the evidence document. The substrate is untouched — a
    rejection is a status change, never a delete or a projection edit. ``RETURNING``
    returns the updated row even if the SELECT policy would hide it (cross-lens).
    """
    cur = await conn.execute(
        f"UPDATE entity_allusion SET status = %s, reviewer = %s "
        f"WHERE id = %s::uuid RETURNING {_RETURN_COLS}",
        (status, reviewer, allusion_id),
    )
    return await cur.fetchone()


# --- optional heuristic proposer (FR-5), batch-side ------------------------

#: The method tag stamped on edges this deterministic proposer emits.
HEURISTIC_METHOD = "heuristic:surface-match-v1"


def propose_allusions_for_document(conn: _Conn, document_id: str) -> dict[str, Any]:
    """Deterministically propose ``alludes_to`` edges for one document's local entities.

    For each **local** projected entity in a document's projecting fiction lens (Block
    D), if a **global** ``canonical`` row shares its normalized surface and carries KB
    identity (``wikidata_id``), propose an ``alludes_to`` edge from the work-local
    entity to that real-world entity. Deterministic (surface + KB match; no model),
    idempotent (``ON CONFLICT DO NOTHING``), and every edge lands ``status='proposed'``
    for review (PALS's Law) — the proposer never confirms. Returns a JSON-able summary.
    """
    global_lens = conn.execute(
        "SELECT id FROM document_collections WHERE name = 'global'"
    ).fetchone()
    if global_lens is None:
        return {"document_id": document_id, "proposed": 0}
    global_lens_id = global_lens["id"]

    # Local entities in this document's projecting scope lenses, matched to a global
    # KB-linked canonical by normalized surface. lens_local_entity carries surface_norm;
    # canonical.preferred is matched case-insensitively (deterministic).
    candidates = conn.execute(
        """
        SELECT DISTINCT ep.lens_id AS from_lens_id, ep.canonical_id AS from_canonical_id,
               c.id AS to_canonical_id
        FROM entity_projection ep
        JOIN lens_local_entity lle
          ON lle.lens_id = ep.lens_id AND lle.canonical_id = ep.canonical_id
        JOIN canonical c
          ON c.wikidata_id IS NOT NULL
         AND lower(c.preferred) = lle.surface_norm
        WHERE ep.document_id = %s AND ep.scope = 'local'
          AND ep.lens_id <> %s AND c.id <> ep.canonical_id
        """,
        (document_id, global_lens_id),
    ).fetchall()

    proposed = 0
    for cand in candidates:
        result = conn.execute(
            f"""
            INSERT INTO entity_allusion
                (from_canonical_id, from_lens_id, to_canonical_id, to_lens_id, edge_type,
                 confidence, method, status, evidence_document_id)
            VALUES (%s, %s, %s, %s, 'alludes_to', 0.5, %s, %s, %s)
            ON CONFLICT {_CONFLICT_KEY} DO NOTHING
            """,
            (
                cand["from_canonical_id"],
                cand["from_lens_id"],
                cand["to_canonical_id"],
                global_lens_id,
                HEURISTIC_METHOD,
                STATUS_PROPOSED,
                document_id,
            ),
        )
        proposed += result.rowcount
    return {"document_id": document_id, "proposed": proposed, "candidates": len(candidates)}
