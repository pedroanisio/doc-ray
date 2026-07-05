"""Presentation helpers for the browser job-status page."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

KIND_LABELS = {
    "ingest": "Upload and ingest",
    "annotate": "Annotate text",
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


def decorate_job_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Copy raw job rows and attach presentation labels/outcome fields."""
    decorated = []
    for row in rows:
        item = dict(row)
        item["kind_label"] = humanize_kind(item.get("kind"))
        item["status_label"] = humanize_status(item.get("status"))
        item["outcome"] = summarize_outcome(item)
        decorated.append(item)
    return decorated
