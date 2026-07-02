"""Worker dispatch tests: run_once with stub + real handlers (skip without a DB).

run_once is the single claim->dispatch->finalize step. Tested with a stub registry
(success / raise) and the real delete handler (pure SQL). Test jobs use priority 0 so
they claim ahead of any default-priority stragglers, keeping assertions deterministic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from doc_ray.adapters import queue_postgres as q
from doc_ray.application.jobs import backoff_seconds, run_once
from doc_ray.domain.jobs import JobStatus

_MIGRATIONS = Path(__file__).resolve().parents[3] / "db" / "migrations"


def _connect_or_skip():
    import _itdb

    try:
        conn = _itdb.connect()
    except Exception as exc:  # pragma: no cover - env dependent
        pytest.skip(f"DB unreachable: {exc}")
    conn.execute((_MIGRATIONS / "020_jobs.sql").read_text())  # idempotent
    return conn


@pytest.fixture
def qconn():
    conn = _connect_or_skip()
    created: list[str] = []
    try:
        yield conn, created
    finally:
        for job_id in created:
            conn.execute("DELETE FROM jobs WHERE id = %s", (job_id,))
        conn.close()


def test_backoff_growth_and_cap():
    assert backoff_seconds(1) == 5.0
    assert backoff_seconds(2) == 10.0
    assert backoff_seconds(3) == 20.0
    assert backoff_seconds(100) == 300.0  # capped


def test_run_once_dispatches_to_handler(qconn):
    conn, created = qconn
    seen: list[str] = []

    def stub(_c, _s, job):
        seen.append(job.id)
        return {"ok": True}

    job_id = q.enqueue(conn, "unit-test", {"x": 1}, priority=0)
    created.append(job_id)
    done = run_once(conn, None, worker_id="w1", handlers={"unit-test": stub})
    assert done is not None and done.id == job_id
    assert seen == [job_id]
    assert q.get(conn, job_id).status is JobStatus.SUCCEEDED


def test_run_once_unknown_kind_is_poison(qconn):
    conn, created = qconn
    job_id = q.enqueue(conn, "nonexistent-kind", {}, priority=0)
    created.append(job_id)
    run_once(conn, None, worker_id="w1", handlers={})
    assert q.get(conn, job_id).status is JobStatus.FAILED  # no retry


def test_run_once_handler_error_requeues(qconn):
    conn, created = qconn

    def boom(_c, _s, _job):
        raise ValueError("handler blew up")

    job_id = q.enqueue(conn, "boom", {}, priority=0)
    created.append(job_id)
    run_once(conn, None, worker_id="w1", handlers={"boom": boom})
    assert q.get(conn, job_id).status is JobStatus.QUEUED  # attempts 1 < 3 -> retry


class _MemStore:
    """In-memory ObjectStore stand-in for upload-ingest tests (no S3/boto3)."""

    bucket = "test-bucket"

    def __init__(self, blobs):
        self._blobs = dict(blobs)

    def ensure_bucket(self):
        return None

    def put(self, key, data, content_type):
        self._blobs[key] = data
        return f"mem://{self.bucket}/{key}"

    def get(self, key):
        return self._blobs[key]


def test_upload_ingest_preserves_original_filename_and_title(qconn):
    # Live-fire regression: the upload path materialized the blob to a random
    # NamedTemporaryFile, so documents.title became 'tmpXXXX' instead of the
    # uploaded file's name. The payload's original_filename and title must win.
    conn, created = qconn
    from doc_ray.application.jobs import HANDLERS

    body = b"Einstein talked about light. The interviewer listened."
    store = _MemStore({"5a/blob.txt": body})
    job_id = q.enqueue(
        conn,
        "ingest",
        {
            "storage_key": "5a/blob.txt",
            "extension": ".txt",
            "original_filename": "einstein_interview.txt",
            "title": "Conversations with Einstein",
        },
        priority=0,
    )
    created.append(job_id)

    done = run_once(conn, store, worker_id="w1", handlers=HANDLERS)
    assert done is not None and q.get(conn, job_id).status is JobStatus.SUCCEEDED
    result = conn.execute("SELECT result FROM jobs WHERE id = %s", (job_id,)).fetchone()["result"]
    doc_id = result["document_id"]
    try:
        row = conn.execute(
            "SELECT title, original_filename FROM documents WHERE id = %s", (doc_id,)
        ).fetchone()
        assert row["original_filename"] == "einstein_interview.txt"  # not tmpXXXX.txt
        assert row["title"] == "Conversations with Einstein"  # explicit payload title wins
        assert "tmp" not in (row["title"] or "").lower()
    finally:
        conn.execute("DELETE FROM documents WHERE id = %s", (doc_id,))


def test_annotate_job_produces_entity_mentions(qconn):
    # Architecture requirement: the WORKER must be able to run Stage-1 NER on
    # demand — an 'annotate' job for a document yields entity_mention rows the
    # canonicalization stages can consume. (Previously NER was host-CLI-only.)
    import _itdb

    from doc_ray.application.jobs import HANDLERS

    conn, created = qconn
    doc_id = _itdb.make_doc(conn, "Elena founded a startup in Berlin. Boxer worked hard.")
    job_id = q.enqueue(conn, "annotate", {}, priority=0, document_id=doc_id)
    created.append(job_id)
    try:
        done = run_once(conn, None, worker_id="w1", handlers=HANDLERS)
        assert done is not None and done.id == job_id
        assert q.get(conn, job_id).status is JobStatus.SUCCEEDED
        n_mentions = conn.execute(
            "SELECT count(*) n FROM entity_mention em JOIN annotations a ON a.id=em.annotation_id "
            "WHERE a.document_id = %s",
            (doc_id,),
        ).fetchone()["n"]
        assert n_mentions > 0  # Berlin / Elena / Boxer should be found
        result = conn.execute("SELECT result FROM jobs WHERE id = %s", (job_id,)).fetchone()[
            "result"
        ]
        assert result and result.get("entities", 0) > 0
    finally:
        conn.execute("DELETE FROM documents WHERE id = %s", (doc_id,))


def test_chain_enqueues_next_kind_on_success(qconn):
    # Pipeline automation: a job whose payload carries chain=["b", "c"] must, on
    # success, enqueue kind "b" for the same document with chain=["c"].
    conn, created = qconn
    import _itdb

    doc_id = _itdb.make_doc(conn, "Chain me. Second sentence.")
    job_id = q.enqueue(
        conn, "step-a", {"chain": ["step-b", "step-c"]}, priority=0, document_id=doc_id
    )
    created.append(job_id)
    try:
        run_once(conn, None, worker_id="w1", handlers={"step-a": lambda c, s, j: {"ok": 1}})
        nxt = conn.execute(
            "SELECT id, payload, document_id FROM jobs WHERE kind = 'step-b' AND document_id = %s",
            (doc_id,),
        ).fetchone()
        assert nxt is not None
        assert nxt["payload"]["chain"] == ["step-c"]
        created.append(nxt["id"])
    finally:
        conn.execute("DELETE FROM documents WHERE id = %s", (doc_id,))


def test_chain_does_not_fire_on_failure(qconn):
    conn, created = qconn

    def boom(_c, _s, _job):
        raise ValueError("nope")

    job_id = q.enqueue(conn, "step-a", {"chain": ["step-b-never"]}, priority=0)
    created.append(job_id)
    run_once(conn, None, worker_id="w1", handlers={"step-a": boom})
    nxt = conn.execute("SELECT 1 FROM jobs WHERE kind = 'step-b-never'").fetchone()
    assert nxt is None  # failed step never advances the pipeline


def test_chain_takes_document_id_from_result(qconn):
    # The ingest job starts with document_id=None; the chained annotate must
    # pick the document id up from the handler's RESULT.
    conn, created = qconn
    import _itdb

    doc_id = _itdb.make_doc(conn, "Result-id chain. Second sentence.")
    job_id = q.enqueue(conn, "fake-ingest", {"chain": ["step-b-res"]}, priority=0)
    created.append(job_id)
    try:
        run_once(
            conn,
            None,
            worker_id="w1",
            handlers={"fake-ingest": lambda c, s, j: {"document_id": doc_id, "status": "ingested"}},
        )
        nxt = conn.execute("SELECT id, document_id FROM jobs WHERE kind = 'step-b-res'").fetchone()
        assert nxt is not None and str(nxt["document_id"]) == doc_id
        created.append(nxt["id"])
    finally:
        conn.execute("DELETE FROM documents WHERE id = %s", (doc_id,))


def test_annotate_job_sets_document_language(qconn):
    # Architecture fix: annotate must set documents.language (which fires the
    # migration-016 FTS rebuild trigger) so the redundant post-annotate
    # reannotate pass is no longer needed for language-aware search.
    import _itdb

    from doc_ray.application.jobs import HANDLERS

    conn, created = qconn
    doc_id = _itdb.make_doc(conn, "Elena founded a startup in Berlin. It grew fast.")
    job_id = q.enqueue(conn, "annotate", {}, priority=0, document_id=doc_id)
    created.append(job_id)
    try:
        run_once(conn, None, worker_id="w1", handlers=HANDLERS)
        assert q.get(conn, job_id).status is JobStatus.SUCCEEDED
        row = conn.execute("SELECT language FROM documents WHERE id = %s", (doc_id,)).fetchone()
        assert row["language"] == "en"
    finally:
        conn.execute("DELETE FROM documents WHERE id = %s", (doc_id,))


def test_delete_handler_removes_document(qconn):
    import _itdb

    conn, created = qconn
    from doc_ray.application.jobs import HANDLERS

    doc_id = _itdb.make_doc(conn, "Delete me. Second sentence.")
    job_id = q.enqueue(conn, "delete", {}, priority=0, document_id=doc_id)
    created.append(job_id)

    run_once(conn, None, worker_id="w1", handlers=HANDLERS)

    gone = conn.execute("SELECT 1 FROM documents WHERE id = %s", (doc_id,)).fetchone()
    assert gone is None  # document (and its cascaded job) removed
