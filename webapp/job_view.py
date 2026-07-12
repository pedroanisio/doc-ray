"""Presentation helpers for the browser job-status page."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

#: A running job whose heartbeat is older than this reads as stalled: the
#: worker died (deploy restart, crash) and the reaper hasn't caught up yet.
#: Kept well above the worker's claim timeout (default 120s) to avoid flapping.
STALLED_AFTER_SECONDS = float(os.environ.get("DOCRAY_WEBAPP_STALLED_AFTER_SECONDS", "300"))

#: Duplicates WORKER_CLAIM_TIMEOUT_SECONDS' default (src/doc_ray/entrypoints/worker.py) —
#: this module stays independent of doc_ray by design (see webapp/operator_health.py's
#: docstring). Past this age the backend already considers the job's heartbeat
#: reclaimable; STALLED_AFTER_SECONDS above is deliberately looser (avoids flapping
#: the active/inactive UI state), so progress display uses this narrower threshold
#: to warn early rather than keep showing confident numbers for a likely-dead worker.
HEARTBEAT_RECLAIM_ELIGIBLE_SECONDS = float(
    os.environ.get("DOCRAY_WEBAPP_HEARTBEAT_RECLAIM_ELIGIBLE_SECONDS", "120")
)

KIND_LABELS = {
    "ingest": "Upload and ingest",
    "annotate": "Annotate text",
    "reannotate": "Refresh word frequency",
    "reextract": "Re-extract",
    "semantic_enrich": "Enrich semantics",
    "canonicalize": "Curate domain entities",
    "backup": "Create backup",
    "restore": "Restore backup",
    "suggest_seeds": "Mine domain candidates",
    "seed": "Seed approved entities",
}

STATUS_LABELS = {
    "queued": "Queued",
    "running": "Running",
    "succeeded": "Completed",
    "failed": "Needs attention",
}

SUCCESS_MESSAGES = {
    "ingest": "The document was uploaded and registered.",
    "annotate": "The document text was annotated.",
    "reannotate": "Word-frequency data was refreshed.",
    "reextract": "The document was re-extracted.",
    "semantic_enrich": "Semantic enrichment completed.",
    "canonicalize": "Domain entity curation completed.",
    "backup": "The backup was created.",
    "restore": "The backup was restored.",
    "suggest_seeds": "Candidate domain entities were mined.",
    "seed": "Approved entities were added to the canonical registry.",
}

NEXT_ACTIONS = {
    "ingest": "Check the file and upload it again after fixing the problem.",
    "annotate": "Restart annotation after the worker or input issue is fixed.",
    "reannotate": "Restart the word-frequency refresh after the worker or input issue is fixed.",
    "reextract": "Restart extraction after the worker or input issue is fixed.",
    "semantic_enrich": "Restart semantic enrichment after the worker or input issue is fixed.",
    "canonicalize": "Review the seeding inputs, then retry curation.",
    "backup": "Check storage access and retry the backup.",
    "restore": "Check the selected backup and storage access before retrying.",
}


def humanize_kind(kind: str | None) -> str:
    """Return a stable user-facing label for a job kind."""
    raw = kind or "job"
    return KIND_LABELS.get(raw, raw.replace("_", " ").title())


def humanize_status(status: str | None) -> str:
    """Return a status label suitable for a status pill."""
    raw = status or "unknown"
    return STATUS_LABELS.get(raw, raw.replace("_", " ").title())


def _reported_error(error: Any) -> str:
    """Extract the useful part of a stored exception/error value."""
    message = str(error or "").strip()
    if not message:
        return "The worker did not report a cause."
    message = next((line.strip() for line in message.splitlines() if line.strip()), message)
    message = re.sub(r"^[A-Za-z_][\w.]*:\s*", "", message)
    repr_match = re.match(r"^[A-Za-z_][\w.]*\((['\"])(.*)\1\)$", message)
    if repr_match:
        message = repr_match.group(2).strip()
    return message.rstrip(".") + "."


def summarize_outcome(job: Mapping[str, Any]) -> dict[str, str | None]:
    """Build human-facing outcome text for one job row."""
    kind = str(job.get("kind") or "job")
    label = humanize_kind(kind)
    status = str(job.get("status") or "unknown")
    if status == "succeeded":
        return {
            "summary": SUCCESS_MESSAGES.get(kind, f"{label} completed."),
            "detail": None,
            "action": None,
        }
    if status == "failed":
        return {
            "summary": f"{label} did not finish.",
            "detail": _reported_error(job.get("error")),
            "action": NEXT_ACTIONS.get(kind, "Fix the reported cause, then retry this step."),
        }
    if status == "queued":
        return {
            "summary": f"{label} is waiting for a worker.",
            "detail": None,
            "action": "This page refreshes while the pipeline is active.",
        }
    if status == "running":
        return {
            "summary": f"{label} is running.",
            "detail": None,
            "action": "Keep this page open for live progress.",
        }
    return {
        "summary": f"{label} is in state {humanize_status(status).lower()}.",
        "detail": None,
        "action": None,
    }


def _now(now: datetime | None = None) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def _seconds_since(value: Any, now: datetime | None = None) -> float | None:
    if not isinstance(value, datetime):
        return None
    stamped = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return (_now(now) - stamped).total_seconds()


def _duration_label(seconds: float) -> str:
    if seconds < 90:
        return f"{max(1, round(seconds))}s"
    if seconds < 90 * 60:
        return f"{max(1, round(seconds / 60))}m"
    return f"{max(1, round(seconds / 3600))}h"


def summarize_progress(
    job: Mapping[str, Any], now: datetime | None = None
) -> dict[str, Any] | None:
    """Build a display model for a job's persisted progress fields.

    No ETA: pipeline stages are not equally sized (stage durations for large
    documents span minutes to hours depending on the stage), so a linear
    elapsed/remaining extrapolation is not "rough" — for the dominant stage it
    is confidently wrong, sometimes by an order of magnitude. Report real
    elapsed time (always true) instead of a fabricated remaining-time guess.
    Also flags ``heartbeat_uncertain`` when the heartbeat is older than the
    worker's own reclaim-eligibility window — the job may already be running
    under a worker that no longer exists, well before the (deliberately
    looser) ``is_stalled``/``STALLED_AFTER_SECONDS`` check would say so.
    """
    current = job.get("progress_current")
    total = job.get("progress_total")
    stage = str(job.get("progress_stage") or "").strip() or None
    try:
        current_int = int(current) if current is not None else None
        total_int = int(total) if total is not None else None
    except (TypeError, ValueError):
        return {"stage": stage} if stage else None
    if current_int is None or total_int is None or total_int <= 0:
        return {"stage": stage} if stage else None
    current_int = max(0, min(current_int, total_int))
    percent = round((current_int / total_int) * 100)
    progress: dict[str, Any] = {
        "current": current_int,
        "total": total_int,
        "percent": percent,
        "label": f"{percent}%",
        "stage": stage,
    }
    is_running = str(job.get("status") or "") == "running"
    elapsed = _seconds_since(job.get("started_at"), now)
    if is_running and elapsed is not None and elapsed >= 5:
        progress["elapsed_seconds"] = elapsed
        progress["elapsed_label"] = _duration_label(elapsed)
    heartbeat_age = _seconds_since(job.get("heartbeat_at"), now)
    if (
        is_running
        and heartbeat_age is not None
        and heartbeat_age > HEARTBEAT_RECLAIM_ELIGIBLE_SECONDS
    ):
        progress["heartbeat_uncertain"] = True
    return progress


def is_stalled(job: Mapping[str, Any], now: datetime | None = None) -> bool:
    """Return whether a *running* job's heartbeat has gone stale."""
    if str(job.get("status") or "") != "running":
        return False
    beat_age = _seconds_since(job.get("heartbeat_at"), now)
    return beat_age is not None and beat_age > STALLED_AFTER_SECONDS


def is_active_job(job: Mapping[str, Any], now: datetime | None = None) -> bool:
    """Return whether a job should block re-running its pipeline step.

    Queued jobs and fresh running jobs are active; a running job with a stale
    heartbeat is a corpse the reaper hasn't collected yet — treating it as
    active would lock the reprocess controls behind a job that will never
    finish (the exact failure mode of a worker killed mid-run by a deploy).
    """
    status = str(job.get("status") or "")
    if status == "queued":
        return True
    return status == "running" and not is_stalled(job, now)


def decorate_job_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Copy raw job rows and attach presentation labels/outcome fields."""
    decorated = []
    for row in rows:
        item = dict(row)
        stalled = is_stalled(row)
        item["stalled"] = stalled
        item["kind_label"] = humanize_kind(item.get("kind"))
        item["status_label"] = (
            "Stalled (worker lost)" if stalled else humanize_status(item.get("status"))
        )
        item["outcome"] = summarize_outcome(item)
        item["progress"] = summarize_progress(item)
        if stalled:
            item["outcome"] = {
                "summary": f"{item['kind_label']} stalled — its worker stopped reporting.",
                "detail": "The job will be reclaimed automatically, or the step can be re-run.",
                "action": "Use the reprocess controls to run this step again.",
            }
        decorated.append(item)
    return decorated
