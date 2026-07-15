"""Domain model for the async job queue (pure, storage-agnostic).

The ``jobs`` table (migration ``020_jobs.sql``) is the source of record; these are
the in-memory shapes the queue adapter maps rows to and from. ``status`` is a closed
lifecycle enum; ``kind`` is deliberately open text (validated by the worker's
dispatch table, not the schema) so new job kinds need no migration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class JobStatus(str, Enum):
    """The closed lifecycle of a job (mirrors the table's CHECK constraint)."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


# Well-known job kinds. `kind` is open text at the storage layer; these are the
# kinds the worker's dispatch table understands today.
KIND_INGEST = "ingest"
KIND_ANNOTATE = "annotate"
KIND_SEMANTIC_ENRICH = "semantic_enrich"
KIND_REANNOTATE = "reannotate"
KIND_DELETE = "delete"
KIND_ARCHIVE_DOCUMENT = "archive_document"
KIND_RESTORE_DOCUMENT = "restore_document"
KIND_ADD_DOCUMENT_TAGS = "add_document_tags"
KIND_SEED_CANONICAL = "seed_canonical"
KIND_SEED_SUGGEST = "seed_suggest"
KIND_SEED_APPROVED = "seed_approved"
KIND_SUGGEST_DOCUMENT_METADATA = "suggest_document_metadata"
KIND_CANONICALIZE = "canonicalize"
KIND_BACKUP = "backup"
KIND_RESTORE = "restore"
KIND_UPDATE_METADATA = "update_metadata"
KIND_REEXTRACT = "reextract"
# Prism Tier-1: detect image/regions in a document and compute deterministic
# visual features (CPU-only; no model). Appended to the post-ingest chain tail.
KIND_VISUAL_EXTRACT = "visual_extract"

# Job kinds that are expected to take enough wall-clock time to need a reserved
# slow lane. Compose worker filters must stay in sync with this set. Visual
# extract renders pages (CPU) so it can be heavy on visual-dominant documents.
LONG_RUNNING_JOB_KINDS = frozenset({KIND_ANNOTATE, KIND_REANNOTATE, KIND_VISUAL_EXTRACT})


@dataclass(frozen=True)
class Job:
    """A claimed unit of background work.

    Attributes:
        id: ULID primary key (time-sortable).
        kind: Job kind; routed by the worker's dispatch table.
        status: Current lifecycle state.
        payload: Kind-specific input parameters.
        attempts: How many times this job has been claimed.
        max_attempts: Attempt ceiling before it is marked ``failed``.
        document_id: Owning document UUID, or ``None`` until known.
        progress_current: Completed progress units, or ``None`` before a report.
        progress_total: Total progress units when known.
        progress_stage: Human-readable current stage.
    """

    id: str
    kind: str
    status: JobStatus
    payload: dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    max_attempts: int = 3
    document_id: str | None = None
    progress_current: int | None = None
    progress_total: int | None = None
    progress_stage: str | None = None
