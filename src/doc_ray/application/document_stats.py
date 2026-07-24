"""The D-024 read-model rollup refresh, shared by every count-mutating job.

Split out of :mod:`doc_ray.application.job_handlers` (D-015) so the handlers that
change a document's counts — and the handler modules split off alongside them —
can refresh the rollups without importing each other.
"""

from __future__ import annotations

import logging
from typing import Any

import psycopg

_logger = logging.getLogger("doc_ray.jobs")


def refresh_document_stats(conn: psycopg.Connection[Any], document_id: object) -> None:
    """Refresh the corpus stat-band rollups after a document's counts change.

    Keeps ``document_stats`` (per-document sentence/occurrence counts) exact and
    debounce-refreshes the tenant's distinct-lexeme scalar (migration 095), so
    ``GET /corpus`` reads precomputed totals instead of scanning the 17.5M-row
    ``lexeme_occurrences`` table on every request.

    Best-effort: the rollup is derived convenience data, so a refresh failure is
    logged and swallowed rather than failing an annotation/ingest job that
    already committed its real work — the migration backfill and the next
    count-mutating job reconcile any drift.
    """
    try:
        conn.execute("SELECT docray_refresh_stats_for_document(%s)", (str(document_id),))
    except Exception:  # pragma: no cover - defensive; refresh must never fail a job
        _logger.warning("corpus stat rollup refresh failed for document %s", document_id)
