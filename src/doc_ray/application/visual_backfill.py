"""Enqueue Prism visual jobs across the corpus (the corpus-wide vision backfill).

Tier-1 (``KIND_VISUAL_EXTRACT``, CPU) can run corpus-wide immediately. Tier-2
(``KIND_VISUAL_EMBED``, GPU) should run once the vision model is served on the
H200. Tier-3 (``KIND_VISUAL_CAPTION``, GPU) drives a VLM once per image and is
the slowest of the three by orders of magnitude — run it last, and never with
``only_missing=False`` unless a re-caption is actually intended. All three go
through the normal job queue (bounded, resumable, RLS-agnostic worker), so a
backfill never bypasses the pipeline. See ``scripts/enqueue_visual_backfill.py``
for the operator CLI.

These selections are deliberately RLS-honest: they see only what the calling
connection's role may read. Corpus-wide operator runs assume the trusted role at
the CLI boundary rather than here, so an API caller importing this module can
never inherit a tenant-blind sweep.
"""

from __future__ import annotations

from typing import Any

import psycopg

from doc_ray.adapters import queue_postgres as queue
from doc_ray.domain.jobs import (
    KIND_VISUAL_CAPTION,
    KIND_VISUAL_EMBED,
    KIND_VISUAL_EXTRACT,
)


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


def enqueue_visual_caption_jobs(
    conn: psycopg.Connection[Any], *, only_missing: bool = True, priority: int = 100
) -> list[str]:
    """Enqueue Tier-3 grounded captioning for documents that have images.

    With ``only_missing`` skips documents that already carry ``visual_caption``
    rows. That guard matters more here than in the tiers below: Tier-3 spends one
    VLM round trip per image, so re-driving a captioned corpus costs hours of GPU
    time rather than seconds. Only documents with at least one ``document_image``
    are eligible; the caption handler itself is idempotent per model.
    """
    missing_clause = (
        "AND NOT EXISTS (SELECT 1 FROM visual_caption vc WHERE vc.document_id = d.id)"
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
        queue.enqueue(conn, KIND_VISUAL_CAPTION, {}, priority=priority, document_id=str(row["id"]))
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
