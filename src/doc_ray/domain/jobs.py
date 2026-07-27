"""Domain model for the async job queue (pure, storage-agnostic).

The ``jobs`` table (migration ``020_jobs.sql``) is the source of record; these are
the in-memory shapes the queue adapter maps rows to and from. ``status`` is a closed
lifecycle enum; ``kind`` is deliberately open text (validated by the worker's
dispatch table, not the schema) so new job kinds need no migration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final, Literal


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
# Block C (#6): classify a document's genre (fiction/non-fiction) from
# deterministic heuristic signals. No model — just the pure classifier over the
# stored full text — so it is not long-running. Writes a pending machine guess to
# document_classification; only a human confirmation asserts a genre.
KIND_CLASSIFY_GENRE = "classify_genre"
# Block D (#8): (re)materialize the entity projection — project(mention, lens) — for
# a document, one lens, or the whole corpus (payload rebuild=true). Deterministic,
# no model; chained after the entity pipeline and enqueued by the projection review
# mutations to apply a confirm/reject promptly. Not long-running per document.
KIND_REFRESH_PROJECTION = "refresh_projection"
# Block E (#9): deterministically propose alludes_to edges for a document — where a
# work-local entity's surface matches a KB-linked global entity. No model; emits
# proposed edges only (never confirms), so it is not long-running. Optional (FR-5):
# the propose/confirm/reject API is the primary path; this is a bulk convenience.
KIND_PROPOSE_ALLUSIONS = "propose_allusions"
# Block F (#10): (re)build the hyper-context rollups for a lens (or the whole corpus,
# payload rebuild=true) — the per-lens entity landscape + co-occurrence relations
# aggregated from projections. Deterministic, no model; chained after the projection
# refresh and invocable by an operator. Not long-running per lens.
KIND_REFRESH_LENS_ROLLUP = "refresh_lens_rollup"
# Block G (#7): derive an explainable confidence from existing corpus facts and
# materialize it on current NER mentions. Deterministic, no model, fast-lane.
KIND_SCORE_MENTIONS = "score_mentions"

# The default post-ingest pipeline: Stage-1 annotation, deterministic semantic
# enrichment, then deterministic canonicalization, then Prism Tier-1 visual
# feature extraction. The deterministic visual tier is appended at the tail so
# entity-linking is never delayed (proposal §5.1); it is CPU-only (no model).
# The frontier resolver and the GPU visual tiers are never chained — model cost
# stays an explicit request (D-018).
#
# It lives here, not in the GraphQL entrypoint, because it is the pipeline every
# ingest path owes a document: the API upload mutation and the operator bulk
# loaders must enqueue the same work, and a second definition is how they drift.
DEFAULT_PIPELINE_CHAIN: tuple[str, ...] = (
    KIND_ANNOTATE,
    KIND_SCORE_MENTIONS,
    KIND_SEMANTIC_ENRICH,
    KIND_CANONICALIZE,
    KIND_VISUAL_EXTRACT,
)

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

# Worker lanes. The deployment runs two pools and the split is exactly the set
# above: the GPU pool serves LONG_RUNNING_JOB_KINDS, the fast pool serves the
# rest (its WORKER_EXCLUDE_KINDS is that same set). Cost attribution needs the
# mapping too — a GPU host-hour and a CPU host-hour are not priced alike — so it
# lives here rather than being restated by each consumer.
# Typed as literals, not bare str: the lane is a closed two-valued set, and
# consumers that model it that way (the operations registry) would otherwise have
# to re-narrow a plain str at every use site.
LANE_GPU: Final[Literal["gpu"]] = "gpu"
LANE_CPU: Final[Literal["cpu"]] = "cpu"


def lane_for_kind(kind: str) -> str:
    """Return the worker lane *kind* is claimed by."""
    return LANE_GPU if kind in LONG_RUNNING_JOB_KINDS else LANE_CPU


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
