"""Allusion-edge job handlers (SLP Block E, #9).

The heuristic proposer's worker entry point. Kept in the application layer (like the
projection and lens-rollup handlers) rather than inline in ``job_handlers`` so that
module stays within its size budget and each SLP block owns its handler.
"""

from __future__ import annotations

from typing import Any, cast

import psycopg

from doc_ray.domain.jobs import Job
from doc_ray.domain.progress import report_progress
from doc_ray.ports.object_store import ObjectStore


def handle_propose_allusions(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Propose ``alludes_to`` edges for a document's local entities (Block E, #9, FR-5).

    Deterministic surface + KB match; emits ``proposed`` edges only (never confirms) —
    the primary path is the propose/confirm/reject API, this is a bulk convenience.
    """
    from doc_ray.adapters import allusion_store

    if job.document_id is None:
        raise RuntimeError("propose_allusions requires a document_id")
    report_progress(0, 1, "Proposing allusion edges")
    return allusion_store.propose_allusions_for_document(
        cast(psycopg.Connection[dict[str, Any]], conn), job.document_id
    )
