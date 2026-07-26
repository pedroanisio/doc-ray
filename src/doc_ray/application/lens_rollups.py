"""Hyper-context rollup refresh orchestration (SLP Block F, #10, FR-4).

Thin application layer over :mod:`doc_ray.adapters.lens_rollup_store`. The rollups
aggregate Block D projections, so a refresh strictly FOLLOWS the projection refresh:
Block D's per-(document, scope-lens) projection refresh chains
:func:`refresh_lens_rollup_for_lens` in place of the superseded stats refresh, and a
full :func:`rebuild_all_lens_rollups` re-derives every projecting scope lens after a
policy change. Idempotent and reproducible (invariant 3): same projections ⇒ identical
rollups.
"""

from __future__ import annotations

import logging
from typing import Any

import psycopg

from doc_ray.adapters import lens_rollup_store
from doc_ray.domain.jobs import Job
from doc_ray.domain.progress import report_progress
from doc_ray.ports.object_store import ObjectStore

_logger = logging.getLogger("doc_ray.jobs")


def refresh_lens_rollup_for_lens(conn: psycopg.Connection[Any], lens_id: str) -> dict[str, Any]:
    """Rebuild both hyper-context rollups for one lens (batch connection)."""
    return lens_rollup_store.refresh_lens_rollup(conn, lens_id)


def refresh_lens_rollup_chained(conn: psycopg.Connection[Any], lens_id: str) -> None:
    """Best-effort rollup refresh chained after a lens's projection refresh (Block D).

    Best-effort like the D-024 rollups: hyper-context is derived convenience data, so a
    refresh failure is logged and swallowed rather than failing the projection stage that
    already committed — the next refresh reconciles.
    """
    try:
        refresh_lens_rollup_for_lens(conn, lens_id)
    except Exception:  # pragma: no cover - defensive; must never fail a projection job
        _logger.warning("lens rollup refresh failed for lens %s", lens_id)


def rebuild_all_lens_rollups(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    """Rebuild rollups for every projecting scope lens (operator full rebuild).

    A projecting scope lens is one with materialized projections; the global lens is
    excluded (its corpus-wide landscape is not a per-lens rollup). Reproducible: the
    stored projections fully determine the output.
    """
    lens_ids = [
        str(r["lens_id"])
        for r in conn.execute(
            "SELECT DISTINCT ep.lens_id "
            "FROM entity_projection ep "
            "JOIN document_collections c ON c.id = ep.lens_id "
            "WHERE c.is_scope AND c.name <> 'global'"
        ).fetchall()
    ]
    total_entities = 0
    total_relations = 0
    for i, lens_id in enumerate(lens_ids):
        report_progress(i, len(lens_ids), "Rebuilding lens rollups")
        summary = refresh_lens_rollup_for_lens(conn, lens_id)
        total_entities += summary["entities"]
        total_relations += summary["relations"]
    return {"lenses": len(lens_ids), "entities": total_entities, "relations": total_relations}


def handle_refresh_lens_rollup(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Job handler: rebuild the hyper-context rollups for one lens or every lens.

    Payload: ``rebuild`` (truthy) → full corpus rebuild; else ``lens_id`` refreshes one
    lens. Enqueue after a projection change, or invoke directly as an operator.
    """
    if job.payload.get("rebuild"):
        report_progress(0, 1, "Rebuilding all lens rollups")
        return rebuild_all_lens_rollups(conn)
    lens_id = job.payload.get("lens_id")
    if not lens_id:
        raise RuntimeError("refresh_lens_rollup requires a lens_id (or rebuild=true)")
    report_progress(0, 1, "Refreshing lens rollup")
    return refresh_lens_rollup_for_lens(conn, str(lens_id))
