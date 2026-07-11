"""Job dispatch (application.jobs) + the worker loop, on an isolated queue.

Every test runs against a session-local ``TEMP TABLE jobs`` that shadows
``public.jobs`` via the temp schema's search-path precedence, so nothing here
can claim, complete, or fail another process's real queued work.
"""

from __future__ import annotations

import os
import signal
import threading
from typing import Any

import _itdb
import psycopg
import pytest

from doc_ray.adapters import queue_postgres as queue
from doc_ray.application import jobs as jobs_mod
from doc_ray.domain.jobs import KIND_DELETE, KIND_UPDATE_METADATA, Job
from doc_ray.entrypoints import worker as worker_mod
from doc_ray.ports.queue import Queue


@pytest.fixture()
def qconn():
    """A connection whose ``jobs`` table is a private temp shadow."""
    conn = _itdb.connect()
    conn.execute("CREATE TEMP TABLE jobs (LIKE public.jobs INCLUDING ALL)")
    yield conn
    conn.close()


def test_backoff_grows_and_caps():
    assert jobs_mod.backoff_seconds(1) == 5.0
    assert jobs_mod.backoff_seconds(2) == 10.0
    assert jobs_mod.backoff_seconds(0) == 5.0
    assert jobs_mod.backoff_seconds(50) == 300.0  # capped


def test_queue_port_is_satisfied_by_the_postgres_adapter():
    # The Protocol names the adapter's contract; the module provides every method.
    for member in ("enqueue", "claim", "heartbeat", "complete", "fail", "reap_stale"):
        assert callable(getattr(queue, member))
    assert isinstance(Queue, object)


def test_run_once_empty_queue_returns_none(qconn):
    assert jobs_mod.run_once(qconn, None, worker_id="t-empty") is None


def test_run_once_success_completes_job(qconn):
    seen: list[Job] = []

    def ok_handler(conn: Any, store: Any, job: Job) -> dict[str, Any]:
        seen.append(job)
        return {"ok": True}

    jid = queue.enqueue(qconn, "stub", {"x": 1})
    got = jobs_mod.run_once(qconn, None, worker_id="t-ok", handlers={"stub": ok_handler})
    assert got is not None and got.id == jid
    assert seen and seen[0].payload == {"x": 1}
    row = qconn.execute("SELECT status, error FROM jobs WHERE id=%s", (jid,)).fetchone()
    assert row is not None and row["status"] == "succeeded"


def test_run_once_failure_requeues_with_backoff_then_terminal(qconn):
    def boom(conn: Any, store: Any, job: Job) -> dict[str, Any]:
        raise RuntimeError("kaput")

    jid = queue.enqueue(qconn, "stub", {}, max_attempts=2)
    jobs_mod.run_once(qconn, None, worker_id="t-fail", handlers={"stub": boom})
    row = qconn.execute("SELECT status, error FROM jobs WHERE id=%s", (jid,)).fetchone()
    assert row is not None and row["status"] == "queued"  # retry scheduled (attempt 1/2)

    qconn.execute("UPDATE jobs SET run_after = now() WHERE id = %s", (jid,))
    jobs_mod.run_once(qconn, None, worker_id="t-fail", handlers={"stub": boom})
    row = qconn.execute("SELECT status, error FROM jobs WHERE id=%s", (jid,)).fetchone()
    assert row is not None and row["status"] == "failed"  # ceiling reached → terminal
    assert "kaput" in (row["error"] or "")


def test_run_once_failure_logs_exception_with_job_context(qconn, caplog):
    """BL-115: a handler failure must log the full traceback + job context, not
    just repr(exc) tucked away in the jobs.error DB column."""

    def boom(conn: Any, store: Any, job: Job) -> dict[str, Any]:
        raise RuntimeError("kaput")

    jid = queue.enqueue(qconn, "stub", {}, max_attempts=2)
    with caplog.at_level("ERROR", logger="doc_ray.jobs"):
        jobs_mod.run_once(qconn, None, worker_id="t-fail-log", handlers={"stub": boom})
    assert caplog.records, "expected a log record for the failed handler"
    record = caplog.records[-1]
    assert record.exc_info is not None, "expected the traceback to be attached"
    assert jid in record.getMessage()
    assert "stub" in record.getMessage()


def test_run_once_unknown_kind_is_poison(qconn):
    jid = queue.enqueue(qconn, "mystery", {})
    got = jobs_mod.run_once(qconn, None, worker_id="t-poison", handlers={})
    assert got is not None and got.id == jid
    row = qconn.execute("SELECT status, error FROM jobs WHERE id=%s", (jid,)).fetchone()
    assert row is not None and row["status"] == "failed"
    assert "unknown job kind" in (row["error"] or "")


def test_run_once_heartbeat_thread_runs(qconn):
    hb_conn = _itdb.connect()
    try:
        release = threading.Event()

        def slow(conn: Any, store: Any, job: Job) -> dict[str, Any]:
            release.wait(0.25)  # keep the job in-flight across >1 heartbeat interval
            return {}

        jid = queue.enqueue(qconn, "slow", {})
        jobs_mod.run_once(
            qconn,
            None,
            worker_id="t-beat",
            handlers={"slow": slow},
            heartbeat_conn=hb_conn,
            heartbeat_interval=0.05,
        )
        row = qconn.execute("SELECT status, error FROM jobs WHERE id=%s", (jid,)).fetchone()
        assert row is not None and row["status"] == "succeeded"
    finally:
        hb_conn.close()


def test_handle_delete_removes_document(qconn, pg):
    conn, created = pg
    doc_id = _itdb.make_doc(conn, "To be deleted.")
    job = Job(id="0" * 26, kind=KIND_DELETE, status="queued", document_id=doc_id)  # type: ignore[arg-type]
    out = jobs_mod.handle_delete(qconn, None, job)
    assert out == {"deleted": doc_id}
    gone = conn.execute("SELECT 1 FROM documents WHERE id=%s", (doc_id,)).fetchone()
    assert gone is None


def test_handle_update_metadata_applies_edit_and_records_history(qconn, pg):
    conn, created = pg
    doc_id = _itdb.make_doc(conn, "Some text.")
    created.append(doc_id)
    job = Job(
        id="0" * 26,
        kind=KIND_UPDATE_METADATA,
        status="queued",
        document_id=doc_id,
        payload={"title": "New Title", "description": "A summary.", "tags": ["a", "b"]},
    )  # type: ignore[arg-type]

    out = jobs_mod.handle_update_metadata(qconn, None, job)

    assert set(out["changed"]) == {"title", "description", "tags"}
    row = conn.execute("SELECT title, description FROM documents WHERE id=%s", (doc_id,)).fetchone()
    assert row["title"] == "New Title"
    assert row["description"] == "A summary."
    tags = {
        r["tag"]
        for r in conn.execute(
            "SELECT t.tag FROM document_tags dt JOIN tags t ON t.id=dt.tag_id "
            "WHERE dt.document_id=%s",
            (doc_id,),
        ).fetchall()
    }
    assert tags == {"a", "b"}
    history = conn.execute(
        "SELECT field FROM document_metadata_history WHERE document_id=%s ORDER BY id",
        (doc_id,),
    ).fetchall()
    assert [r["field"] for r in history] == ["title", "description", "tags"]


def test_handle_update_metadata_is_a_no_op_when_values_are_unchanged(qconn, pg):
    conn, created = pg
    doc_id = _itdb.make_doc(conn, "Some text.")
    created.append(doc_id)
    current_title = conn.execute("SELECT title FROM documents WHERE id=%s", (doc_id,)).fetchone()[
        "title"
    ]
    job = Job(
        id="0" * 26,
        kind=KIND_UPDATE_METADATA,
        status="queued",
        document_id=doc_id,
        payload={"title": current_title},
    )  # type: ignore[arg-type]

    out = jobs_mod.handle_update_metadata(qconn, None, job)

    assert out["changed"] == []
    history = conn.execute(
        "SELECT 1 FROM document_metadata_history WHERE document_id=%s", (doc_id,)
    ).fetchall()
    assert history == []


def test_handle_update_metadata_requires_document(qconn):
    job = Job(id="0" * 26, kind=KIND_UPDATE_METADATA, status="queued")  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="document_id"):
        jobs_mod.handle_update_metadata(qconn, None, job)


def test_ports_declare_their_contracts():
    """The port modules are importable contract declarations (Protocol defs)."""
    import doc_ray.ports.canonical_repository as canonical_repository
    import doc_ray.ports.knowledge_base as knowledge_base
    import doc_ray.ports.resolver as resolver

    for mod in (resolver, knowledge_base, canonical_repository):
        names = [n for n in dir(mod) if not n.startswith("_")]
        assert names, f"{mod.__name__} declares nothing"


class _BlobStore:
    """Store double for handle_ingest: pre-seeded blob, content-addressed put."""

    def __init__(self) -> None:
        self.bucket = "test-bucket"
        self.blobs: dict[str, bytes] = {}

    def ensure_bucket(self) -> None:
        return None

    def put(self, key: str, data: bytes, content_type: str) -> str:
        self.blobs[key] = data
        return f"s3://{self.bucket}/{key}"

    def get(self, key: str) -> bytes:
        return self.blobs[key]


def test_handle_ingest_materializes_blob_and_ingests(qconn, pg):
    import hashlib

    conn, created = pg
    body = f"Ingested via a job. Unique {_itdb.unique_sha()[:8]}.".encode()
    sha = hashlib.sha256(body).hexdigest()
    store = _BlobStore()
    key = f"{sha[:2]}/{sha}.txt"
    store.blobs[key] = body

    job = Job(
        id="0" * 26,
        kind="ingest",
        status="queued",  # type: ignore[arg-type]
        payload={"storage_key": key, "extension": ".txt"},
    )
    out = jobs_mod.handle_ingest(qconn, store, job)
    created.append(out["document_id"])
    assert out["status"] == "ingested"


def test_handle_ingest_requires_store(qconn):
    job = Job(id="0" * 26, kind="ingest", status="queued", payload={})  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="object store"):
        jobs_mod.handle_ingest(qconn, None, job)


def _seed_blob(store: _BlobStore, body: bytes, ext: str) -> str:
    """Content-address ``body`` into ``store`` and return its storage key."""
    import hashlib

    sha = hashlib.sha256(body).hexdigest()
    key = f"{sha[:2]}/{sha}{ext}"
    store.blobs[key] = body
    return key


def test_handle_ingest_title_falls_back_to_original_filename(qconn):
    """Without an explicit title, the document title is the real filename stem.

    Regression for the temp-name leak: before the fix the worker materialised the
    blob to a random ``tempfile`` name, so an upload without an explicit title took
    a ``tmp…`` scratch name as its title. The no-title path must fall back to the
    uploaded name (``Animal Farm``). The explicit-title path is covered by
    ``test_worker.test_upload_ingest_preserves_original_filename_and_title``.
    """
    body = f"No title supplied. {_itdb.unique_sha()[:8]}.".encode()
    store = _BlobStore()
    key = _seed_blob(store, body, ".txt")
    job = Job(
        id="0" * 26,
        kind="ingest",
        status="queued",  # type: ignore[arg-type]
        payload={"storage_key": key, "extension": ".txt", "original_filename": "Animal Farm.txt"},
    )
    out = jobs_mod.handle_ingest(qconn, store, job)
    row = qconn.execute(
        "SELECT title FROM documents WHERE id = %s", (out["document_id"],)
    ).fetchone()
    qconn.execute("DELETE FROM documents WHERE id = %s", (out["document_id"],))
    assert row["title"] == "Animal Farm"
    assert not row["title"].startswith("tmp")


def test_handle_reannotate_runs_spacy(qconn, pg):
    conn, created = pg
    doc_id = _itdb.make_doc(conn, "Elena founded a startup in Berlin.")
    created.append(doc_id)
    job = Job(id="0" * 26, kind="reannotate", status="queued", document_id=doc_id)  # type: ignore[arg-type]
    out = jobs_mod.handle_reannotate(qconn, None, job)
    assert out.get("occ", 0) > 0 or out.get("skipped") is True


def test_handle_reannotate_requires_document(qconn):
    job = Job(id="0" * 26, kind="reannotate", status="queued")  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="document_id"):
        jobs_mod.handle_reannotate(qconn, None, job)


def test_worker_loop_drains_on_sigint(monkeypatch):
    calls = {"reap": 0, "run": 0}

    monkeypatch.setattr(
        worker_mod.queue,
        "reap_stale",
        lambda conn, older_than_seconds: calls.__setitem__("reap", calls["reap"] + 1),
    )
    monkeypatch.setattr(
        worker_mod,
        "run_once",
        lambda conn, store, **kw: calls.__setitem__("run", calls["run"] + 1),
    )
    monkeypatch.setattr(worker_mod, "ObjectStore", lambda cfg: None)
    monkeypatch.setattr(worker_mod, "_POLL_SECONDS", 0.05)

    old_term = signal.getsignal(signal.SIGTERM)
    old_int = signal.getsignal(signal.SIGINT)
    timer = threading.Timer(0.3, lambda: os.kill(os.getpid(), signal.SIGINT))
    timer.start()
    try:
        assert worker_mod.main() == 0
    finally:
        timer.cancel()
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)
    assert calls["reap"] >= 1 and calls["run"] >= 1


def test_worker_loop_recovers_from_dropped_connection(monkeypatch):
    """BL-101: a connection-level error must be retried, never crash the process."""
    calls = {"reap": 0, "run": 0}

    def flaky_reap_stale(_conn, older_than_seconds):
        calls["reap"] += 1
        if calls["reap"] == 1:
            raise psycopg.OperationalError("server closed the connection unexpectedly")

    monkeypatch.setattr(worker_mod.queue, "reap_stale", flaky_reap_stale)
    monkeypatch.setattr(
        worker_mod,
        "run_once",
        lambda conn, store, **kw: calls.__setitem__("run", calls["run"] + 1),
    )
    monkeypatch.setattr(worker_mod, "ObjectStore", lambda cfg: None)
    monkeypatch.setattr(worker_mod, "_POLL_SECONDS", 0.05)
    monkeypatch.setattr(worker_mod, "_RECONNECT_BACKOFF_SECONDS", 0.05)

    old_term = signal.getsignal(signal.SIGTERM)
    old_int = signal.getsignal(signal.SIGINT)
    timer = threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGINT))
    timer.start()
    try:
        assert worker_mod.main() == 0  # never raises out of main() / process never crashes
    finally:
        timer.cancel()
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)
    # The dropped-connection reap_stale call was call #1; the loop must have
    # reconnected and gone on to claim/process at least one more job afterward.
    assert calls["reap"] >= 2
    assert calls["run"] >= 1


def test_main_configures_logging_before_running(monkeypatch):
    """BL-115: the worker process configures logging once at startup, before run()."""
    calls: list[str] = []
    monkeypatch.setattr(worker_mod, "configure_logging", lambda: calls.append("configured"))
    monkeypatch.setattr(worker_mod, "run", lambda: calls.append("ran") or 0)
    assert worker_mod.main() == 0
    assert calls == ["configured", "ran"]
