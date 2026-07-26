"""The projection refresh — materialize ``project(mention, lens)`` (SLP Block D, #8).

This is the application-layer orchestration behind the pure resolver
(:mod:`doc_ray.domain.projection`). It reads a document's substrate facts, mints the
lens-local identities the resolver needs, runs the resolver per mention per
projecting lens, and persists the derived ``entity_projection`` rows plus the
per-lens rollup — the D-024 rollup pattern applied to entity identity (never
computed live on a read path; refreshed at the pipeline boundary).

Refresh is **incremental** per document (chained after canonicalize / reannotate /
apply_corrections, so a substrate change is never left stale) and supports a full
**rebuild** for policy changes (operator-invoked). A rebuild is reproducible: the
stable ``lens_local_entity`` registry means the same substrate + lens + policy always
yields identical rows (invariant 3).

⚠ ARCHITECTURAL CONTRACT (PALS's LAW) — projections are proposals, not facts. See
:mod:`doc_ray.adapters.projection_store`.
"""

from __future__ import annotations

import logging
from typing import Any

import psycopg

from doc_ray.adapters import projection_store
from doc_ray.domain.genre_policy import Genre, ResolutionScope, resolution_scope
from doc_ray.domain.jobs import Job
from doc_ray.domain.progress import report_progress
from doc_ray.domain.projection import Lens, Mention, local_key, project
from doc_ray.ports.object_store import ObjectStore

_logger = logging.getLogger("doc_ray.jobs")

#: Cost control (FR-2): the maximum number of lenses one document is projected
#: through in a single refresh. Projecting every lens for every mention at 10k
#: documents is not viable; a document over the bound is projected through its first
#: N lenses (``global`` first, then by name) and the drop is LOGGED, never silent.
MAX_PROJECTING_LENSES_PER_DOCUMENT = 8


def _local_keys_for(
    mentions: list[Mention], genre: Genre, lens: projection_store.LensRow
) -> list[tuple[str, str, str]]:
    """Return the distinct ``(surface_norm, type, display)`` keys that resolve LOCAL.

    Uses the same Block C policy the resolver uses, so the set the adapter mints is
    exactly the set :func:`project` will look up — the decision is single-sourced in
    :func:`resolution_scope`. The global lens mints nothing (it forces GLOBAL).
    """
    if lens.is_global:
        return []
    seen: dict[tuple[str, str], str] = {}
    for m in mentions:
        if resolution_scope(genre, m.entity_type) is ResolutionScope.LOCAL:
            norm, etype = local_key(m.surface, m.entity_type)
            seen.setdefault((norm, etype), m.surface.strip() or m.surface)
    return [(norm, etype, display) for (norm, etype), display in seen.items()]


def _project_document_into_lens(
    conn: psycopg.Connection[Any],
    document_id: str,
    lens: projection_store.LensRow,
    mentions: list[Mention],
    genre: Genre,
    global_index: dict[str, Any],
) -> int:
    """Materialize one document's projection into one lens; return rows written."""
    local_candidates = projection_store.ensure_local_candidates(
        conn, lens.lens_id, _local_keys_for(mentions, genre, lens)
    )
    domain_lens = Lens(lens_id=lens.lens_id, is_global=lens.is_global)
    projections = [
        project(
            m,
            domain_lens,
            genre,
            local_candidates=local_candidates,
            global_index=global_index,
        )
        for m in mentions
    ]
    written = projection_store.replace_projections(conn, document_id, lens.lens_id, projections)
    projection_store.apply_reviews(conn, document_id, lens.lens_id)
    if not lens.is_global:
        # Block F (#10): the hyper-context rollups aggregate these projections, so they
        # are refreshed here, strictly AFTER the projection is written. Best-effort —
        # derived convenience data must never fail the projection stage.
        from doc_ray.application.lens_rollups import refresh_lens_rollup_chained

        refresh_lens_rollup_chained(conn, lens.lens_id)
    return written


def refresh_projections_for_document(
    conn: psycopg.Connection[Any], document_id: str, *, lens_id: str | None = None
) -> dict[str, Any]:
    """Refresh a document's projection through its projecting lenses (incremental).

    Args:
        conn: Batch connection (``docray_batch``).
        document_id: The document to (re)project.
        lens_id: Restrict the refresh to a single lens; ``None`` refreshes all of the
            document's projecting lenses (bounded by
            :data:`MAX_PROJECTING_LENSES_PER_DOCUMENT`).

    Returns:
        A JSON-able summary: lenses projected, rows written, dropped-over-bound count.
    """
    genre = projection_store.get_document_genre(conn, document_id)
    inputs = projection_store.load_projection_inputs(conn, document_id)
    lenses, dropped = projection_store.projecting_lenses_for_document(
        conn, document_id, max_lenses=MAX_PROJECTING_LENSES_PER_DOCUMENT
    )
    if lens_id is not None:
        lenses = [ln for ln in lenses if ln.lens_id == lens_id]
    if dropped:
        _logger.warning(
            "document %s exceeds the projecting-lens bound (%d); dropped %d lens(es)",
            document_id,
            MAX_PROJECTING_LENSES_PER_DOCUMENT,
            dropped,
        )
    total = 0
    for lens in lenses:
        total += _project_document_into_lens(
            conn, document_id, lens, inputs.mentions, genre, inputs.global_index
        )
    return {
        "document_id": document_id,
        "genre": genre.value,
        "lenses_projected": len(lenses),
        "projections_written": total,
        "lenses_dropped_over_bound": dropped,
    }


def refresh_projections_chained(conn: psycopg.Connection[Any], document_id: str) -> None:
    """Rebuild a document's projections after its mentions/links change (Block D, #8).

    Chained after the entity-finalizing stages (canonicalize / reannotate /
    apply_corrections) so a projection is never left stale relative to the substrate.
    Best-effort like ``refresh_document_stats``: a projection is derived convenience
    data, so a refresh failure is logged and swallowed rather than failing a pipeline
    stage that already committed its real work — the next refresh reconciles.
    """
    try:
        refresh_projections_for_document(conn, document_id)
    except Exception:  # pragma: no cover - defensive; refresh must never fail a job
        _logger.warning("projection refresh failed for document %s", document_id)


def rebuild_all_projections(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    """Drop and rebuild every projection from the substrate (operator full rebuild).

    Reproducible (invariant 3): the ``lens_local_entity`` registry is preserved, so
    re-minting reuses the same local ``canonical`` rows and the rebuilt rows are
    identical. Used after a policy change (e.g. a genre re-classification) that a
    per-document incremental refresh would not otherwise pick up corpus-wide.
    """
    conn.execute("TRUNCATE entity_projection")
    # The Block F hyper-context rollups are derived from the projections; clear them so a
    # full rebuild reconstructs them from scratch (the per-document refresh below re-chains
    # the rollup refresh for each projecting scope lens).
    conn.execute("TRUNCATE lens_entity_rollup")
    conn.execute("TRUNCATE lens_entity_relation")
    doc_ids = [
        str(r["document_id"])
        for r in conn.execute(
            "SELECT DISTINCT a.document_id "
            "FROM entity_mention em JOIN annotations a ON a.id = em.annotation_id"
        ).fetchall()
    ]
    total = 0
    for i, document_id in enumerate(doc_ids):
        report_progress(i, len(doc_ids), "Rebuilding projections")
        total += refresh_projections_for_document(conn, document_id)["projections_written"]
    return {"documents": len(doc_ids), "projections_written": total}


def handle_refresh_projection(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Job handler: (re)materialize projections for a document, one lens, or the corpus.

    Payload knobs:
        ``rebuild`` (truthy): full corpus rebuild (ignores ``document_id``).
        ``lens_id``: restrict an incremental refresh to a single lens.

    Enqueued by the projection review mutations (to apply a confirm/reject promptly)
    and chained after the entity pipeline; also invocable directly by an operator.
    """
    if job.payload.get("rebuild"):
        report_progress(0, 1, "Rebuilding all projections")
        return rebuild_all_projections(conn)
    if job.document_id is None:
        raise RuntimeError("refresh_projection requires a document_id (or rebuild=true)")
    report_progress(0, 1, "Refreshing document projections")
    return refresh_projections_for_document(
        conn, job.document_id, lens_id=job.payload.get("lens_id")
    )
