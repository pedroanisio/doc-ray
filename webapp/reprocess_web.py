"""Document reprocess controls: what an operator may re-run, and its live state.

Split out of ``webapp/document_web.py`` (D-015). Every entry is one pipeline
step reachable from the document page, carrying the GraphQL mutation that
enqueues it, the job kinds that represent it in the queue, and the document
states it applies to — a repair is offered only where there is damage, a
refinement only where there is text to refine.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

try:
    from webapp.job_view import is_active_job, summarize_outcome, summarize_progress
except ModuleNotFoundError:  # pragma: no cover - direct ``python webapp/app.py`` path.
    from job_view import is_active_job, summarize_outcome, summarize_progress

#: The webapp's row-returning query helper (``webapp.app.q``), passed in rather
#: than imported so this module stays free of the late-binding dance.
QueryFn = Callable[..., list[dict[str, Any]]]


_REPROCESS_CONFIG = {
    # Listed first: on a failed document it is the only step that can do
    # anything, and it is the one the error box links to.
    "retry_extract": {
        "label": "Retry extraction",
        "description": (
            "Re-read the stored original with the current extractor — including OCR "
            "for a scanned file — and, if text is found, promote the document and "
            "run the full pipeline. This is the only way to recover a failed "
            "ingest: re-uploading the identical file is recognised by content hash "
            "and fails again."
        ),
        "mutation": "mutation($id: ID!) { retryDocumentExtraction(documentId: $id) { id } }",
        "response_key": "retryDocumentExtraction",
        "active_kinds": {"retry_extract"},
        "pdf_only": False,
        # A repair, not a refinement: offered only where there is damage.
        "statuses": {"failed"},
    },
    "reextract": {
        "label": "Re-extract",
        "description": "Rebuild text and page geometry from the original PDF.",
        "mutation": "mutation($id: ID!) { reextractDocument(documentId: $id) { id } }",
        "response_key": "reextractDocument",
        "active_kinds": {"reextract"},
        "pdf_only": True,
        # Every refinement step needs text to work on.
        "statuses": {"extracted"},
    },
    "annotate": {
        "label": "Re-annotate",
        "description": (
            "Run full annotation (tokens, part-of-speech, named entities) again. "
            "This deletes and replaces entity mentions — re-run Re-canonicalize "
            "afterward if this document has been canonicalized."
        ),
        "mutation": "mutation($id: ID!) { annotateDocument(documentId: $id) { id } }",
        "response_key": "annotateDocument",
        "active_kinds": {"annotate"},
        "pdf_only": False,
        # Every refinement step needs text to work on.
        "statuses": {"extracted"},
    },
    "reannotate": {
        "label": "Refresh word frequency",
        "description": (
            "Recompute word-frequency/lexicon data from the current text, without "
            "re-running full annotation. Does not touch entities or part-of-speech."
        ),
        "mutation": "mutation($id: ID!) { reannotate(documentId: $id) { id } }",
        "response_key": "reannotate",
        "active_kinds": {"reannotate"},
        "pdf_only": False,
        # Every refinement step needs text to work on.
        "statuses": {"extracted"},
    },
    "semantic_enrich": {
        "label": "Re-enrich",
        "description": "Recompute deterministic semantic enrichment.",
        "mutation": "mutation($id: ID!) { semanticEnrichDocument(documentId: $id) { id } }",
        "response_key": "semanticEnrichDocument",
        "active_kinds": {"semantic_enrich"},
        "pdf_only": False,
        # Every refinement step needs text to work on.
        "statuses": {"extracted"},
    },
    "canonicalize": {
        "label": "Re-canonicalize",
        "description": "Re-run deterministic entity canonicalization.",
        "mutation": "mutation($id: ID!) { canonicalizeDocument(documentId: $id) { id } }",
        "response_key": "canonicalizeDocument",
        "active_kinds": {"canonicalize"},
        "pdf_only": False,
        # Every refinement step needs text to work on.
        "statuses": {"extracted"},
    },
}


def active_reprocess_jobs(q: QueryFn, doc_id: str) -> dict[str, dict]:
    """Return active pipeline jobs keyed by reprocess step.

    A running job whose heartbeat went stale (worker killed mid-run) is
    excluded — it must not block re-running the step it will never finish.
    """
    rows = q(
        """
        SELECT id, kind, status, progress_current, progress_total, progress_stage,
               started_at, heartbeat_at
        FROM jobs
        WHERE document_id = %s AND status IN ('queued', 'running')
        ORDER BY id DESC
        """,
        (doc_id,),
    )
    active: dict[str, dict] = {}
    for row in rows:
        if not is_active_job(row):
            continue
        for step, cfg in _REPROCESS_CONFIG.items():
            if row.get("kind") in cfg["active_kinds"] and step not in active:
                active[step] = row
    return active


def failed_reprocess_jobs(q: QueryFn, doc_id: str) -> dict[str, dict]:
    """Return the most recent job per reprocess step, if that attempt failed.

    ``active_reprocess_jobs`` only sees queued/running jobs, so a step whose
    last attempt permanently failed (retries exhausted, or reclaimed after a
    stale heartbeat) looked identical to a step that was never run — the
    reprocess control just offered to run it again with no explanation. A
    step is only reported here when its single most recent job (of any
    status) is the failed one; once a later attempt succeeds, the failure
    is superseded and stops showing.
    """
    rows = q(
        """
        SELECT id, kind, status, attempts, max_attempts, error
        FROM jobs
        WHERE document_id = %s
        ORDER BY id DESC
        """,
        (doc_id,),
    )
    latest_seen: set[str] = set()
    failed: dict[str, dict] = {}
    for row in rows:
        for step, cfg in _REPROCESS_CONFIG.items():
            if row.get("kind") not in cfg["active_kinds"] or step in latest_seen:
                continue
            latest_seen.add(step)
            if row.get("status") == "failed":
                failed[step] = row
    return failed


def _step_applies_to_status(cfg: dict, doc: dict) -> bool:
    """Whether *cfg* is offered for this document's status.

    A step without a ``statuses`` restriction is offered regardless, as every
    step was before the repair path existed.
    """
    statuses = cfg.get("statuses")
    if not statuses:
        return True
    return str(doc.get("status") or "").lower() in statuses


def reprocess_steps(q: QueryFn, doc: dict, doc_id: str) -> list[dict]:
    """Build document-page reprocess controls from live job state."""
    active_jobs = active_reprocess_jobs(q, doc_id)
    failed_jobs = failed_reprocess_jobs(q, doc_id)
    is_pdf = str(doc.get("doc_type") or "").lower() == "pdf"
    steps = []
    for step, cfg in _REPROCESS_CONFIG.items():
        available = (is_pdf or not cfg["pdf_only"]) and _step_applies_to_status(cfg, doc)
        active = active_jobs.get(step)
        failed = failed_jobs.get(step) if not active else None
        steps.append(
            {
                "step": step,
                "label": cfg["label"],
                "description": cfg["description"],
                "available": available,
                "active": active,
                "active_status": active.get("status") if active else None,
                "progress": summarize_progress(active) if active else None,
                "outcome": summarize_outcome(failed) if failed else None,
                "attempts": failed.get("attempts") if failed else None,
                "max_attempts": failed.get("max_attempts") if failed else None,
            }
        )
    return steps
