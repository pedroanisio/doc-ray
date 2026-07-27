"""Application service and worker job for explainable NER mention confidence."""

from __future__ import annotations

from typing import Any

import psycopg

from doc_ray.adapters import mention_confidence_store as store
from doc_ray.domain.jobs import Job
from doc_ray.domain.mention_confidence import (
    rarity_signal,
    registry_novelty_signal,
    score,
    sentence_boundary_signal,
    type_disagreement_signal,
)
from doc_ray.domain.progress import report_progress
from doc_ray.ports.object_store import ObjectStore

HEURISTIC_METHOD = "heuristic:v1"


def score_document(conn: psycopg.Connection[Any], document_id: str) -> dict[str, Any]:
    """Score and materialize every current NER mention for one document.

    The adapter supplies tenant-scoped corpus facts from precomputed rollups;
    the pure domain scorer supplies deterministic arithmetic and explanations.
    Re-running with unchanged facts writes identical values.
    """
    facts = store.load_mention_facts(conn, document_id)
    materialized: list[store.StoredScore] = []
    for item in facts:
        confidence, reasons = score(
            item.mention,
            [
                type_disagreement_signal(item.type_counts),
                registry_novelty_signal(resolved=item.resolved),
                rarity_signal(occurrence_count=item.occurrence_count),
                sentence_boundary_signal(crosses_boundary=item.crosses_sentence_boundary),
            ],
        )
        materialized.append(
            store.StoredScore(
                mention_id=item.mention_id,
                confidence=confidence,
                method=HEURISTIC_METHOD,
                reasons=tuple(reasons),
            )
        )
    stored = store.persist_scores(conn, materialized)
    return {"document_id": document_id, "scored": stored, "method": HEURISTIC_METHOD}


def handle_score_mentions(
    conn: psycopg.Connection[Any], store_adapter: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """Materialize mention review confidence for one document."""
    if job.document_id is None:
        raise RuntimeError("score_mentions requires a document_id")
    report_progress(0, 1, "Scoring entity mentions")
    result = score_document(conn, job.document_id)
    report_progress(1, 1, "Entity mentions scored")
    return result
