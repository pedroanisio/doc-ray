"""Job dispatch: claim a job and route it to a handler (D-005).

The worker is a synchronous process; this module holds the kind→handler dispatch
table and the single claim→run→finalize step (:func:`run_once`). Handlers lazily
import the blocking machinery (spaCy for reannotation, the ingest pipeline) so this
module stays importable — and :func:`run_once` stays unit-testable with a stub
registry — without those heavy dependencies present.
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import UTC, datetime
from types import FrameType
from typing import TYPE_CHECKING, Any

import psycopg

from doc_ray.adapters import queue_postgres as queue
from doc_ray.adapters import telemetry_store
from doc_ray.adapters.compute_device import describe_device
from doc_ray.application.backup import (
    handle_backup as handle_backup,
)
from doc_ray.application.backup import (
    handle_restore as handle_restore,
)
from doc_ray.application.job_handlers import (
    HANDLERS as HANDLERS,
)
from doc_ray.application.job_handlers import (
    Handler as Handler,
)
from doc_ray.application.job_handlers import (
    handle_add_document_tags as handle_add_document_tags,
)
from doc_ray.application.job_handlers import (
    handle_annotate as handle_annotate,
)
from doc_ray.application.job_handlers import (
    handle_archive_document as handle_archive_document,
)
from doc_ray.application.job_handlers import (
    handle_canonicalize as handle_canonicalize,
)
from doc_ray.application.job_handlers import (
    handle_delete as handle_delete,
)
from doc_ray.application.job_handlers import (
    handle_ingest as handle_ingest,
)
from doc_ray.application.job_handlers import (
    handle_reannotate as handle_reannotate,
)
from doc_ray.application.job_handlers import (
    handle_reextract as handle_reextract,
)
from doc_ray.application.job_handlers import (
    handle_restore_document as handle_restore_document,
)
from doc_ray.application.job_handlers import (
    handle_seed_approved as handle_seed_approved,
)
from doc_ray.application.job_handlers import (
    handle_seed_canonical as handle_seed_canonical,
)
from doc_ray.application.job_handlers import (
    handle_seed_suggest as handle_seed_suggest,
)
from doc_ray.application.job_handlers import (
    handle_semantic_enrich as handle_semantic_enrich,
)
from doc_ray.application.job_handlers import (
    handle_suggest_document_metadata as handle_suggest_document_metadata,
)
from doc_ray.application.job_handlers import (
    handle_update_metadata as handle_update_metadata,
)
from doc_ray.correlation import request_id_from_payload
from doc_ray.domain import telemetry
from doc_ray.domain.jobs import (
    KIND_INGEST,
    Job,
    lane_for_kind,
)

# The reporter lives in domain.progress (BL-123) so adapters can report
# phase boundaries without importing this module; every handler (and the
# worker tests) still addresses it as ``jobs.report_progress``.
from doc_ray.domain.progress import (
    report_progress,
    reset_progress_reporter,
    set_progress_reporter,
)
from doc_ray.domain.retry_policy import (
    FailureClass,
)
from doc_ray.domain.retry_policy import (
    backoff_seconds as retry_backoff,
)
from doc_ray.domain.retry_policy import (
    classify as classify_failure,
)
from doc_ray.ports.object_store import ObjectStore

_logger = logging.getLogger("doc_ray.jobs")

if TYPE_CHECKING:
    # Concrete adapter type for the (path-based) pipeline, imported for typing only
    # so this module stays importable without boto3.
    pass


_DEFAULT_JOB_TIMEOUT_SECONDS = 3600.0


class JobExecutionTimeout(TimeoutError):
    """Raised when a running handler exceeds its configured wall-clock budget."""


def backoff_seconds(attempts: int) -> float:
    """Return the exponential backoff (capped) for the *attempts*-th failure.

    The schedule for a failure whose cause is unknown or deterministic. A failure
    that names an exhausted shared resource gets the far longer schedule in
    :mod:`doc_ray.domain.retry_policy` instead — see :func:`retry_delay_for`.
    """
    return retry_backoff(attempts, FailureClass.DETERMINISTIC)


def retry_delay_for(exc: BaseException, *, attempts: int, max_attempts: int) -> float | None:
    """Return the delay before retrying after *exc*, or ``None`` when terminal.

    Classifying before scheduling is what keeps a busy GPU from consuming a job's
    whole attempt budget in fifteen seconds; :mod:`doc_ray.domain.retry_policy`
    documents the incident that made this necessary.
    """
    if attempts >= max_attempts:
        return None
    return retry_backoff(attempts, classify_failure(exc))


def _timeout_value(raw: float | str | None) -> float | None:
    if raw is None:
        return None
    seconds = float(raw)
    return seconds if seconds > 0 else None


def execution_timeout_seconds(
    kind: str, job_timeouts: Mapping[str, float | None] | None = None
) -> float | None:
    """Return the configured execution timeout for *kind*.

    ``WORKER_JOB_TIMEOUT_SECONDS`` sets the default. A kind-specific
    ``WORKER_JOB_TIMEOUT_<KIND>_SECONDS`` override wins, where non-alphanumeric
    characters in ``kind`` become underscores. Values <= 0 disable the timeout.
    """
    if job_timeouts is not None and kind in job_timeouts:
        return _timeout_value(job_timeouts[kind])
    env_kind = "".join(ch if ch.isalnum() else "_" for ch in kind).upper()
    per_kind = os.environ.get(f"WORKER_JOB_TIMEOUT_{env_kind}_SECONDS")
    if per_kind is not None:
        return _timeout_value(per_kind)
    default = os.environ.get("WORKER_JOB_TIMEOUT_SECONDS", _DEFAULT_JOB_TIMEOUT_SECONDS)
    return _timeout_value(default)


def _job_request_id(job: Job) -> str:
    return request_id_from_payload(job.payload) or "-"


def _retry_label(retry_in_seconds: float | None) -> str:
    if retry_in_seconds is None:
        return "terminal failure"
    return f"retry in {retry_in_seconds:g}s"


@contextlib.contextmanager
def _enforce_execution_timeout(job: Job, seconds: float | None) -> Iterator[None]:
    """Raise :class:`JobExecutionTimeout` if *job* exceeds *seconds*.

    The production worker calls ``run_once`` on the main thread, where POSIX
    interval timers can interrupt blocking Python code such as ``time.sleep`` or
    network reads. Test helper threads and non-POSIX platforms fall back to no
    alarm rather than installing process-global signal state from the wrong
    thread.
    """
    if (
        seconds is None
        or seconds <= 0
        or threading.current_thread() is not threading.main_thread()
        or not hasattr(signal, "SIGALRM")
        or not hasattr(signal, "ITIMER_REAL")
    ):
        yield
        return

    def _raise_timeout(_signum: int, _frame: FrameType | None) -> None:
        raise JobExecutionTimeout(
            f"job {job.id} ({job.kind}) exceeded {seconds:g}s execution timeout"
        )

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _raise_timeout)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        signal.signal(signal.SIGALRM, previous_handler)


def _start_heartbeat(
    conn: psycopg.Connection[Any],
    job: Job,
    worker_id: str,
    interval: float,
    shutdown: threading.Event | None = None,
    conn_lock: threading.Lock | None = None,
) -> Callable[[], None]:
    """Refresh *job*'s heartbeat on a daemon thread; return a stop() callable.

    Uses a dedicated connection (never the one the handler is using — psycopg
    connections are not safe for concurrent use); *conn_lock* serializes that
    connection with the handler thread's progress reports, which share it (see
    ``run_once``). When *shutdown* is set while the handler is still running,
    the thread also stamps the job's drain request (once): if the process is
    later SIGKILLed mid-handler, the reaper releases the job without spending
    an attempt instead of treating the deploy-kill as a crash (see
    ``queue.request_drain``/``queue.reap_stale``).
    """
    stop = threading.Event()
    lock = conn_lock or threading.Lock()

    def _beat() -> None:
        drain_stamped = False
        since_beat = 0.0
        # Wake in short slices rather than full intervals: the drain stamp must
        # land within ~1s of shutdown, or a deploy whose stop-grace is shorter
        # than the heartbeat interval kills the process before the stamp — which
        # turns every such deploy back into an attempt-burning "crash" (observed
        # live 2026-07-12: workers killed seconds after SIGTERM never stamped).
        step = min(1.0, interval)
        while not stop.wait(step):
            if shutdown is not None and shutdown.is_set() and not drain_stamped:
                with contextlib.suppress(Exception), lock:
                    queue.request_drain(conn, job.id, worker_id=worker_id)
                    drain_stamped = True
            since_beat += step
            if since_beat >= interval:
                since_beat = 0.0
                with contextlib.suppress(Exception), lock:
                    queue.heartbeat(conn, job.id, worker_id=worker_id)

    thread = threading.Thread(target=_beat, daemon=True)
    thread.start()

    def _stop() -> None:
        stop.set()
        thread.join(timeout=1.0)

    return _stop


def run_once(
    conn: psycopg.Connection[Any],
    store: ObjectStore | None,
    *,
    worker_id: str,
    handlers: dict[str, Handler] | None = None,
    heartbeat_conn: psycopg.Connection[Any] | None = None,
    heartbeat_interval: float = 15.0,
    job_timeouts: Mapping[str, float | None] | None = None,
    shutdown: threading.Event | None = None,
    only_kinds: Sequence[str] | None = None,
    exclude_kinds: Sequence[str] | None = None,
    disabled_kinds: Sequence[str] | None = None,
) -> Job | None:
    """Claim one job, dispatch it, and finalize it; return the job (or ``None``).

    An unknown kind is a poison message → failed immediately (no retry). Any handler
    exception fails the job, requeuing with exponential backoff until the attempt
    ceiling is reached, after which it is terminal. When *heartbeat_conn* is given,
    the claimed job's heartbeat is refreshed on a side thread for the handler's
    duration (so a long job is not reaped mid-run).

    Args:
        conn: Sync database connection (autocommit).
        store: Object store for blob handlers, or ``None`` when unused.
        worker_id: Identifier recorded as the claim holder.
        handlers: Dispatch table; defaults to :data:`HANDLERS`.
        heartbeat_conn: Dedicated connection for the heartbeat thread, or ``None``.
        heartbeat_interval: Seconds between heartbeats.
        job_timeouts: Optional per-kind timeout overrides; values <= 0 disable.
        shutdown: The worker loop's shutdown event; when it is set mid-handler,
            the heartbeat thread stamps a drain request on the job so a
            SIGKILL after the stop-grace window does not spend an attempt.
        only_kinds: When set, claim only these job kinds.
        exclude_kinds: When set, never claim these job kinds (a short-job
            lane worker passes the long-running kinds here).
        disabled_kinds: Job kinds switched off by configuration. Unlike
            ``exclude_kinds`` — which routes work to a different lane and leaves
            it claimable there — a disabled kind is claimed and immediately
            *canceled*, so rows enqueued before the switch was thrown drain out
            instead of sitting queued forever waiting for a lane that will never
            take them.

    Returns:
        The claimed :class:`Job`, or ``None`` if the queue was empty.
    """
    registry = handlers if handlers is not None else HANDLERS
    job = queue.claim(conn, worker_id=worker_id, only_kinds=only_kinds, exclude_kinds=exclude_kinds)
    if job is None:
        _logger.debug("worker %s found no queued job", worker_id)
        return None
    request_id = _job_request_id(job)
    _logger.info(
        "worker %s claimed job %s (kind=%s, document_id=%s, attempt=%d/%d, request_id=%s)",
        worker_id,
        job.id,
        job.kind,
        job.document_id,
        job.attempts,
        job.max_attempts,
        request_id,
    )
    if disabled_kinds is not None and job.kind in set(disabled_kinds):
        _logger.warning(
            "worker %s canceled job %s (kind=%s is disabled by configuration, "
            "document_id=%s, request_id=%s)",
            worker_id,
            job.id,
            job.kind,
            job.document_id,
            request_id,
        )
        queue.cancel(
            conn,
            job.id,
            reason=f"job kind {job.kind!r} is disabled by configuration",
        )
        return job
    handler = registry.get(job.kind)
    if handler is None:
        _logger.error(
            "worker %s rejected job %s (unknown kind %s, document_id=%s, "
            "attempt=%d/%d, request_id=%s)",
            worker_id,
            job.id,
            job.kind,
            job.document_id,
            job.attempts,
            job.max_attempts,
            request_id,
        )
        queue.fail(conn, job.id, error=f"unknown job kind {job.kind!r}", retry_in_seconds=None)
        return job
    side_lock = threading.Lock()
    stop_heartbeat = (
        _start_heartbeat(heartbeat_conn, job, worker_id, heartbeat_interval, shutdown, side_lock)
        if heartbeat_conn is not None
        else None
    )
    last_progress: tuple[int | None, int | None, str | None] = (None, None, None)

    def _report(current: int | None, total: int | None, stage: str | None) -> None:
        nonlocal last_progress
        last_progress = (current, total, stage)
        # Progress must never ride the main connection while a handler holds a
        # transaction open there: the UPDATE would join that transaction and
        # hold the jobs-row lock until commit, blocking the heartbeat on the
        # same row until the reaper stole the job from a living worker
        # (BL-135: the backup handler's snapshot transaction did exactly this).
        # The side connection commits each report immediately; side_lock
        # serializes it with the heartbeat thread, which shares it.
        if heartbeat_conn is not None:
            with side_lock:
                queue.update_progress(
                    heartbeat_conn,
                    job.id,
                    current=current,
                    total=total,
                    stage=stage,
                    worker_id=worker_id,
                )
            return
        queue.update_progress(
            conn,
            job.id,
            current=current,
            total=total,
            stage=stage,
            worker_id=worker_id,
        )

    token = set_progress_reporter(_report)
    started_at = datetime.now(UTC)
    outcome = telemetry.OUTCOME_FAILED
    work_units: int | None = None
    try:
        report_progress(0, None, f"Starting {job.kind}")
        with _enforce_execution_timeout(job, execution_timeout_seconds(job.kind, job_timeouts)):
            result = handler(conn, store, job)
        # One transaction: success recording and chain advancement commit (or
        # roll back) together. A crash or enqueue failure here leaves the job
        # 'running', so the reaper requeues it and the step re-runs — no silent
        # pipeline stall, and no recorded-success-without-successor window.
        work_units = telemetry.work_units_from_result(result)
        with conn.transaction():
            if queue.is_canceled(conn, job.id):
                _logger.info(
                    "worker %s job %s (kind=%s, request_id=%s) was canceled before completion",
                    worker_id,
                    job.id,
                    job.kind,
                    request_id,
                )
                outcome = telemetry.OUTCOME_CANCELED
                return job
            _, total, _ = last_progress
            if total is None:
                total = last_progress[0] or 1
            report_progress(total, total, "Completed")
            if queue.complete(conn, job.id, result=result, worker_id=worker_id):
                outcome = telemetry.OUTCOME_SUCCEEDED
                if (
                    job.document_id is None
                    and isinstance(result, dict)
                    and result.get("document_id")
                ):
                    # Backfill the row (ingest starts with no document): progress
                    # surfaces filter on jobs.document_id and must include step 1.
                    conn.execute(
                        "UPDATE jobs SET document_id = %s WHERE id = %s",
                        (str(result["document_id"]), job.id),
                    )
                _advance_chain(conn, job, result)
                _logger.info(
                    "worker %s completed job %s (kind=%s, document_id=%s, request_id=%s)",
                    worker_id,
                    job.id,
                    job.kind,
                    job.document_id
                    or (str(result["document_id"]) if result.get("document_id") else None),
                    request_id,
                )
            else:
                _logger.warning(
                    "worker %s could not complete job %s (kind=%s, document_id=%s, "
                    "request_id=%s); claim may have been lost",
                    worker_id,
                    job.id,
                    job.kind,
                    job.document_id,
                    request_id,
                )
    except Exception as exc:  # the worker must survive any handler failure
        failure_class = classify_failure(exc)
        retry = retry_delay_for(exc, attempts=job.attempts, max_attempts=job.max_attempts)
        # BL-115: repr(exc) in the DB column is easy to miss (an operator has to
        # think to query it); the full traceback + job context goes to the log
        # too, so a real crash during ingest leaves a durable, discoverable trace.
        _logger.error(
            "job %s (kind=%s, document_id=%s, worker=%s, request_id=%s) "
            "failed on attempt %d/%d [%s] (%s): %s",
            job.id,
            job.kind,
            job.document_id,
            worker_id,
            request_id,
            job.attempts,
            job.max_attempts,
            failure_class.value,
            _retry_label(retry),
            exc,
            exc_info=exc,
        )
        queue.fail(conn, job.id, error=repr(exc), retry_in_seconds=retry)
    finally:
        reset_progress_reporter(token)
        if stop_heartbeat is not None:
            stop_heartbeat()
        # Accounting, not correctness: recorded for every attempt — a failed one
        # consumed the same billed machine as a successful one, and the retry
        # that follows overwrites the jobs row that would have shown it.
        _record_telemetry(conn, job, worker_id, started_at, outcome, work_units)
    return job


def _record_telemetry(
    conn: psycopg.Connection[Any],
    job: Job,
    worker_id: str,
    started_at: datetime,
    outcome: str,
    work_units: int | None,
) -> None:
    """Append one execution record for this attempt (never raises)."""
    elapsed_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
    telemetry_store.record_execution(
        conn,
        telemetry.ExecutionRecord(
            job_id=job.id,
            kind=job.kind,
            lane=lane_for_kind(job.kind),
            outcome=outcome,
            attempt=job.attempts,
            document_id=job.document_id,
            worker_id=worker_id,
            host=worker_id.split(":", 1)[0],
            device=describe_device(),
            work_units=work_units,
            duration_ms=max(0, elapsed_ms),
            started_at=started_at,
        ),
    )


def _advance_chain(conn: psycopg.Connection[Any], job: Job, result: dict[str, Any]) -> None:
    """Enqueue the next pipeline step from the payload's ``chain``, if any.

    The chain travels in the payload (``["annotate", "canonicalize"]``): each
    successful step enqueues the next kind for the same document with the
    remaining chain — deterministic and auditable in the jobs table. An ingest
    step starts with no ``document_id``: the chained step picks it up from the
    handler's result, and the ingest job's own row is backfilled so progress
    surfaces (``jobs(documentId:)``, the webapp cascade) include it. A failed
    step never advances (the retry path re-runs the same step, so the chain
    survives transient failures), and an ingest that did not actually ingest
    (extraction failure, duplicate re-upload) ends the chain rather than
    re-processing.
    """
    chain = list(job.payload.get("chain") or [])
    if not chain:
        return
    if job.kind == KIND_INGEST and result.get("status") != "ingested":
        return
    document_id = job.document_id or (
        result.get("document_id") if isinstance(result, dict) else None
    )
    if document_id is None:
        return
    meta = job.payload.get("_meta")
    enqueue_chain_head(conn, str(document_id), chain, meta=meta if isinstance(meta, dict) else None)


def enqueue_chain_head(
    conn: psycopg.Connection[Any],
    document_id: str,
    chain: Sequence[str],
    *,
    meta: Mapping[str, Any] | None = None,
) -> str | None:
    """Enqueue *chain*'s first step for *document_id*, carrying the rest along.

    The queue has no dependency edges: a chain is expressed by each step's
    payload naming the steps still to come, and :func:`_advance_chain` enqueues
    the next one when a step succeeds. This is the single entry point for
    starting such a chain, so a caller that ingests outside the queue (the
    operator bulk loaders) hands its new document to exactly the same pipeline
    an API upload gets.

    Args:
        conn: Live database connection (must be able to write ``jobs``).
        document_id: The document every step in the chain acts on.
        chain: Job kinds in execution order; an empty chain enqueues nothing.
        meta: Request metadata (``_meta``) to propagate down the chain.

    Returns:
        The id of the enqueued head job, or ``None`` for an empty chain.
    """
    steps = list(chain)
    if not steps:
        return None
    payload: dict[str, Any] = {"chain": steps[1:]}
    if meta is not None:
        payload["_meta"] = dict(meta)
    return queue.enqueue(conn, steps[0], payload, document_id=document_id)
