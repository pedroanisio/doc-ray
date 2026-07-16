"""Enqueue Prism visual jobs across the corpus (the corpus-wide vision backfill).

Tier-1 (``KIND_VISUAL_EXTRACT``, CPU) can run corpus-wide immediately. Tier-2
(``KIND_VISUAL_EMBED``, GPU) should run once the vision model is served on the
H200. Both go through the normal job queue (bounded, resumable, RLS-agnostic
worker), so a backfill never bypasses the pipeline. See
``scripts/enqueue_visual_backfill.py`` for the operator CLI.
"""

from __future__ import annotations

from typing import Any

import psycopg

from doc_ray.adapters import queue_postgres as queue
from doc_ray.domain.jobs import KIND_VISUAL_EMBED, KIND_VISUAL_EXTRACT


def enqueue_visual_extract_jobs(
    conn: psycopg.Connection[Any], *, only_missing: bool = True, priority: int = 100
) -> list[str]:
    """Enqueue Tier-1 visual extraction for extracted PDFs (CPU; safe corpus-wide).

    With ``only_missing`` (default) skips documents that already have
    ``document_image`` rows, so a re-run only fills gaps.
    """
    missing_clause = (
        "AND NOT EXISTS (SELECT 1 FROM document_image di WHERE di.document_id = d.id)"
        if only_missing
        else ""
    )
    rows = conn.execute(
        f"""
        SELECT d.id
        FROM documents d
        WHERE d.status = 'extracted' AND d.doc_type = 'pdf' AND d.source_uri IS NOT NULL
          {missing_clause}
        ORDER BY d.created_at, d.id
        """
    ).fetchall()
    return [
        queue.enqueue(conn, KIND_VISUAL_EXTRACT, {}, priority=priority, document_id=str(row["id"]))
        for row in rows
    ]


def enqueue_visual_embed_jobs(
    conn: psycopg.Connection[Any], *, only_missing: bool = True, priority: int = 100
) -> list[str]:
    """Enqueue Tier-2 embedding for documents that have images (GPU; needs the model).

    With ``only_missing`` skips documents that already have ``image_embedding``
    rows. Only documents with at least one ``document_image`` are eligible.
    """
    missing_clause = (
        "AND NOT EXISTS (SELECT 1 FROM image_embedding ie WHERE ie.document_id = d.id)"
        if only_missing
        else ""
    )
    rows = conn.execute(
        f"""
        SELECT d.id
        FROM documents d
        WHERE d.status = 'extracted'
          AND EXISTS (SELECT 1 FROM document_image di WHERE di.document_id = d.id)
          {missing_clause}
        ORDER BY d.created_at, d.id
        """
    ).fetchall()
    return [
        queue.enqueue(conn, KIND_VISUAL_EMBED, {}, priority=priority, document_id=str(row["id"]))
        for row in rows
    ]
