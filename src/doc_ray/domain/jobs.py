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
# Prism Tier-2: embed a document's images + sentences into one shared vector
# space for cross-modal retrieval (GPU dual encoder). Never auto-chained — model
# cost stays an explicit request (D-018); triggered per-document on demand.
KIND_VISUAL_EMBED = "visual_embed"
# Prism Tier-3: grounded VLM captions with the PALS gate (commit vs review). The
# only tier with untrusted model output; never auto-chained (D-018).
KIND_VISUAL_CAPTION = "visual_caption"
# NLP Layer 20: embed a document's sentences with a learned sentence encoder
# (sentence-transformers) into the `embedding` table — the sole writer after the
# hard cutover from hash-BoW. Heavy, worker-only dependency; never auto-chained —
# model cost stays an explicit request, mirroring KIND_VISUAL_EMBED (D-018).
KIND_SEMANTIC_EMBED = "semantic_embed"
# Block A: re-apply the durable human NER-correction log onto a document's
# current ML mentions (entity_mention is worker-owned; docray_api can only
# record the correction, so it enqueues this cheap job). No spaCy — just the
# deterministic reconcile, so it is not long-running.
KIND_APPLY_CORRECTIONS = "apply_corrections"

# Job kinds that are expected to take enough wall-clock time to need a reserved
# slow lane. Compose worker filters must stay in sync with this set. Visual
# extract renders pages (CPU), while visual and semantic embedding load large
# encoders and process whole documents.
LONG_RUNNING_JOB_KINDS = frozenset(
    {
        KIND_ANNOTATE,
        KIND_REANNOTATE,
        KIND_VISUAL_EXTRACT,
        KIND_VISUAL_EMBED,
        KIND_SEMANTIC_EMBED,
    }
)

# Corpus-wide backup/restore, gated as one unit by ``Config.backup_enabled``
# (``DOCRAY_BACKUP_ENABLED``). Every enforcement point — scheduler, GraphQL
# mutations, archive download, worker dispatch — reads this set rather than
# naming the kinds again, so the toggle cannot be half-applied.
BACKUP_JOB_KINDS = frozenset({KIND_BACKUP, KIND_RESTORE})


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
