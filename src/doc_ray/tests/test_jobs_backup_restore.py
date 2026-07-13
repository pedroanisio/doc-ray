"""Backup / restore / delete-blob job-handler tests (skip without a DB).

Split out of test_jobs_pipeline.py (D-015). Object-store and backup-module
collaborators are stubbed; the ``qconn`` fixture yields ``(conn, created)``
against a 020_jobs.sql-seeded connection, mirroring the sibling file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from doc_ray.adapters import queue_postgres as q
from doc_ray.application.jobs import handle_delete, run_once
from doc_ray.domain.jobs import Job, JobStatus

_MIGRATIONS = Path(__file__).resolve().parents[3] / "db" / "migrations"


class _NoopKb:
    """Stand-in for the KB clients' ``.close()`` contract (no network, no-op teardown)."""

    def close(self):
        """No-op teardown."""
        return None


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


def test_handle_backup_requires_an_object_store():
    from doc_ray.application.jobs import handle_backup
    from doc_ray.domain.jobs import Job, JobStatus

    job = Job(id="x", kind="backup", status=JobStatus.RUNNING, document_id=None)
    with pytest.raises(RuntimeError, match="backup requires an object store"):
        handle_backup(None, None, job)


def test_handle_backup_delegates_to_backup_module(qconn, monkeypatch):
    conn, created = qconn
    from doc_ray import __version__
    from doc_ray.application.jobs import HANDLERS

    summary = {
        "id": "b1",
        "object_key": "backups/b1.tar.gz",
        "archive_bytes": 42,
        "table_rows": 7,
        "blob_count": 2,
        "doc_count": 1,
        "schema_version": __version__,
        "blob_bytes": 99,
    }
    seen = {}
    monkeypatch.setattr(
        "doc_ray.application.backup.create_backup",
        lambda _c, _s, note: seen.update(note=note) or summary,
    )
    job_id = q.enqueue(conn, "backup", {"note": "nightly"}, priority=0)
    created.append(job_id)
    run_once(conn, _NoopKb(), worker_id="w1", handlers=HANDLERS)
    assert q.get(conn, job_id).status is JobStatus.SUCCEEDED
    assert seen == {"note": "nightly"}
    result = conn.execute("SELECT result FROM jobs WHERE id = %s", (job_id,)).fetchone()["result"]
    # only the compact subset is recorded on the job row
    assert result == {
        "id": "b1",
        "object_key": "backups/b1.tar.gz",
        "archive_bytes": 42,
        "table_rows": 7,
        "blob_count": 2,
        "doc_count": 1,
    }


def test_handle_restore_requires_an_object_store():
    from doc_ray.application.jobs import handle_restore
    from doc_ray.domain.jobs import Job, JobStatus

    job = Job(
        id="x",
        kind="restore",
        status=JobStatus.RUNNING,
        document_id=None,
        payload={"backup_id": "b1"},
    )
    with pytest.raises(RuntimeError, match="restore requires an object store"):
        handle_restore(None, None, job)


def test_handle_restore_requires_a_backup_id():
    from doc_ray.application.jobs import handle_restore
    from doc_ray.domain.jobs import Job, JobStatus

    job = Job(id="x", kind="restore", status=JobStatus.RUNNING, document_id=None, payload={})
    with pytest.raises(RuntimeError, match="restore requires a backup_id"):
        handle_restore(None, _NoopKb(), job)


def test_handle_restore_delegates_to_backup_module(qconn, monkeypatch):
    conn, created = qconn
    from doc_ray.application.jobs import HANDLERS

    seen = {}
    monkeypatch.setattr(
        "doc_ray.application.backup.restore_backup",
        lambda _c, _s, backup_id: (
            seen.update(backup_id=backup_id) or {"tables_restored": 3, "blobs_restored": 1}
        ),
    )
    job_id = q.enqueue(conn, "restore", {"backup_id": "b1"}, priority=0)
    created.append(job_id)
    run_once(conn, _NoopKb(), worker_id="w1", handlers=HANDLERS)
    assert q.get(conn, job_id).status is JobStatus.SUCCEEDED
    assert seen == {"backup_id": "b1"}
    result = conn.execute("SELECT result FROM jobs WHERE id = %s", (job_id,)).fetchone()["result"]
    assert result == {"tables_restored": 3, "blobs_restored": 1}


# ---------------------------------------------------------------------------
# BL-097 — deleteDocument must not leak the document's blob in the object
# store forever.
# ---------------------------------------------------------------------------


class _FakeStore:
    """Records .delete() calls; never touches real S3/MinIO."""

    bucket = "documents"

    def __init__(self) -> None:
        self.deleted_keys: list[str] = []

    def delete(self, key: str) -> None:
        self.deleted_keys.append(key)


def test_handle_delete_removes_the_documents_blob(qconn):
    import _itdb

    conn, _created = qconn
    doc_id = _itdb.make_doc(conn, "Delete me.", source_uri="s3://documents/ab/cd.pdf")
    store = _FakeStore()
    job = Job(id="x", kind="delete", status=JobStatus.RUNNING, document_id=doc_id)

    result = handle_delete(conn, store, job)

    assert result == {"deleted": doc_id, "soft": True}
    assert store.deleted_keys == []
    row = conn.execute("SELECT lifecycle_state FROM documents WHERE id = %s", (doc_id,)).fetchone()
    assert row["lifecycle_state"] == "deleted"


def test_handle_delete_tolerates_a_missing_object_store(qconn):
    """The DB delete must still succeed even when no store is configured."""
    import _itdb

    conn, _created = qconn
    doc_id = _itdb.make_doc(conn, "Delete me too.", source_uri="s3://documents/ab/ce.pdf")
    job = Job(id="x", kind="delete", status=JobStatus.RUNNING, document_id=doc_id)

    result = handle_delete(conn, None, job)

    assert result == {"deleted": doc_id, "soft": True}
    row = conn.execute("SELECT lifecycle_state FROM documents WHERE id = %s", (doc_id,)).fetchone()
    assert row["lifecycle_state"] == "deleted"


def test_handle_delete_ignores_a_source_uri_outside_the_configured_bucket(qconn):
    """A foreign-bucket source_uri must not be handed to store.delete() as a bare key."""
    import _itdb

    conn, _created = qconn
    doc_id = _itdb.make_doc(conn, "Odd URI.", source_uri="s3://other-bucket/ab/cf.pdf")
    store = _FakeStore()
    job = Job(id="x", kind="delete", status=JobStatus.RUNNING, document_id=doc_id)

    handle_delete(conn, store, job)

    assert store.deleted_keys == []
