"""Semantic-enrichment worker job wiring tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from doc_ray.adapters import queue_postgres as q
from doc_ray.application.jobs import HANDLERS, handle_semantic_enrich, run_once
from doc_ray.domain.jobs import Job, JobStatus

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


def test_handle_semantic_enrich_dispatches_to_service(qconn, monkeypatch):
    import _itdb

    conn, created = qconn
    doc_id = _itdb.make_doc(conn, "Semantic job. It happened in 2026.")
    seen = {}
    monkeypatch.setattr(
        "doc_ray.application.semantic_enrichment.annotate_document",
        lambda _conn, did: (
            seen.update(document_id=did)
            or {
                "discourse_relations": 1,
                "timexes": 1,
                "events": 1,
                "sentiments": 1,
                "embeddings": 2,
            }
        ),
    )
    job_id = q.enqueue(conn, "semantic_enrich", {}, priority=0, document_id=doc_id)
    created.append(job_id)
    try:
        run_once(conn, None, worker_id="w1", handlers=HANDLERS)
        assert q.get(conn, job_id).status is JobStatus.SUCCEEDED
        result = conn.execute("SELECT result FROM jobs WHERE id = %s", (job_id,)).fetchone()[
            "result"
        ]
        assert seen == {"document_id": doc_id}
        assert result["embeddings"] == 2
    finally:
        conn.execute("DELETE FROM documents WHERE id = %s", (doc_id,))


def test_handle_semantic_enrich_requires_document_id():
    job = Job(id="x", kind="semantic_enrich", status=JobStatus.RUNNING, document_id=None)
    with pytest.raises(RuntimeError, match="semantic_enrich requires a document_id"):
        handle_semantic_enrich(None, None, job)
