"""The registry of corpus-wide maintenance operations an operator can drive.

An *operation* is a named, corpus-scoped action that fans out into ordinary queue
jobs — never a side channel around the pipeline. Each entry carries what an
operator needs in order to decide, not just to execute: which lane it burns (a
GPU sweep and a CPU sweep are not interchangeable), whether it is expensive
enough to warrant an explicit confirmation, and what "already done" means for it.

The registry is metadata only. Counting what is outstanding requires the corpus
and therefore lives in ``doc_ray.application.operations``; keeping the two apart
is what lets this module be imported by the API, the scheduler, and the CLI
without any of them inheriting a database dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from doc_ray.domain.jobs import (
    KIND_REEXTRACT,
    KIND_RETRY_EXTRACT,
    KIND_SEMANTIC_EMBED,
    KIND_VISUAL_CAPTION,
    KIND_VISUAL_EMBED,
    KIND_VISUAL_EXTRACT,
    LANE_CPU,
    LANE_GPU,
)

Lane = Literal["cpu", "gpu"]

#: Backfills are background work by definition. Enqueuing them at the default
#: priority (100) would tie them with user-facing pipeline jobs and, because the
#: queue breaks ties by ULID, push every subsequent ingest behind a sweep that can
#: take a day to drain. 500 keeps normal work preemptive — the same convention the
#: semantic re-embed runs already use.
BACKFILL_PRIORITY = 500


class UnknownOperationError(KeyError):
    """The requested operation key is not in the registry."""


@dataclass(frozen=True, slots=True)
class Operation:
    """One corpus-wide maintenance action."""

    key: str
    label: str
    job_kind: str
    lane: Lane
    summary: str
    #: What ``only_missing=True`` skips, phrased for the console.
    completeness: str
    #: True when a full run costs enough that the UI must confirm before firing.
    expensive: bool = False
    default_priority: int = BACKFILL_PRIORITY


OPERATIONS: tuple[Operation, ...] = (
    Operation(
        key="visual_extract",
        label="Visual extract (Tier 1)",
        job_kind=KIND_VISUAL_EXTRACT,
        lane=LANE_CPU,
        summary="Deterministic image extraction from PDFs. Safe to run corpus-wide.",
        completeness="Skips documents that already have extracted images.",
    ),
    Operation(
        key="visual_embed",
        label="Visual embed (Tier 2)",
        job_kind=KIND_VISUAL_EMBED,
        lane=LANE_GPU,
        summary=(
            "CLIP image and text towers over every extracted image. Runtime tracks "
            "document length, not image count, because the text tower re-embeds "
            "every sentence."
        ),
        completeness="Skips documents that already have image embeddings.",
    ),
    Operation(
        key="visual_caption",
        label="Visual caption (Tier 3)",
        job_kind=KIND_VISUAL_CAPTION,
        lane=LANE_GPU,
        summary=(
            "Grounded VLM captions — one model round trip per image, and the most "
            "expensive work in the system by orders of magnitude."
        ),
        completeness="Skips documents that already have captions.",
        expensive=True,
    ),
    Operation(
        key="semantic_embed",
        label="Semantic sentence embed",
        job_kind=KIND_SEMANTIC_EMBED,
        lane=LANE_GPU,
        summary="Learned sentence embeddings powering semantic search.",
        completeness="Skips documents whose sentences are already embedded.",
    ),
    Operation(
        key="reextract",
        label="PDF geometry re-extract",
        job_kind=KIND_REEXTRACT,
        lane=LANE_CPU,
        summary="Re-derives page geometry for PDFs so spans carry page numbers.",
        completeness="Skips documents that already have page geometry.",
    ),
    Operation(
        key="retry_extract",
        label="Retry failed extraction",
        job_kind=KIND_RETRY_EXTRACT,
        lane=LANE_CPU,
        summary=(
            "Re-reads the stored original of every document whose ingest found no "
            "text and promotes the ones the current extractor can read — the sweep "
            "to run after enabling OCR, since re-uploading the same bytes cannot."
        ),
        completeness="Only documents left in status='failed' are eligible.",
    ),
)

_BY_KEY: dict[str, Operation] = {op.key: op for op in OPERATIONS}

OPERATION_KEYS: tuple[str, ...] = tuple(op.key for op in OPERATIONS)


def get_operation(key: str) -> Operation:
    """Return the registered operation, or raise :class:`UnknownOperationError`.

    Callers reach this with operator-supplied input from GraphQL and from stored
    schedule rows, so an unknown key is a normal rejection path rather than an
    assertion — a schedule whose operation was retired must fail loudly at run
    time instead of silently enqueuing nothing.
    """
    try:
        return _BY_KEY[key]
    except KeyError as exc:
        raise UnknownOperationError(
            f"unknown operation {key!r}; known operations: {', '.join(OPERATION_KEYS)}"
        ) from exc
