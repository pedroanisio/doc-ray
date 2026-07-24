"""Job handler behavior tests (delete/restore/tags/metadata/ingest/reannotate).

Split out of test_jobs_worker.py (D-015); runs on the same TEMP-table ``jobs``
shadow from _jobs_worker_common.py.
"""

from __future__ import annotations

import _itdb
import pytest

from doc_ray.adapters import queue_postgres as queue
from doc_ray.application import jobs as jobs_mod
from doc_ray.domain.jobs import (
    KIND_ADD_DOCUMENT_TAGS,
    KIND_DELETE,
    KIND_RESTORE_DOCUMENT,
    KIND_SUGGEST_DOCUMENT_METADATA,
    KIND_UPDATE_METADATA,
    Job,
)


def test_handle_delete_soft_deletes_document_and_records_history(qconn, pg):
    conn, created = pg
    doc_id = _itdb.make_doc(conn, "To be deleted.")
    job = Job(id="0" * 26, kind=KIND_DELETE, status="queued", document_id=doc_id)  # type: ignore[arg-type]
    out = jobs_mod.handle_delete(qconn, None, job)
    assert out == {"deleted": doc_id, "soft": True}
    row = conn.execute(
        "SELECT lifecycle_state, deleted_at FROM documents WHERE id=%s", (doc_id,)
    ).fetchone()
    assert row["lifecycle_state"] == "deleted"
    assert row["deleted_at"] is not None
    history = conn.execute(
        "SELECT field, old_value, new_value FROM document_metadata_history "
        "WHERE document_id=%s ORDER BY id",
        (doc_id,),
    ).fetchall()
    assert [(r["field"], r["old_value"], r["new_value"]) for r in history] == [
        ("lifecycle_state", "active", "deleted")
    ]


def test_handle_restore_document_restores_soft_deleted_document(qconn, pg):
    conn, created = pg
    doc_id = _itdb.make_doc(conn, "To be restored.")
    created.append(doc_id)
    jobs_mod.handle_delete(
        qconn,
        None,
        Job(id="0" * 26, kind=KIND_DELETE, status="queued", document_id=doc_id),  # type: ignore[arg-type]
    )
    job = Job(
        id="1" * 26,
        kind=KIND_RESTORE_DOCUMENT,
        status="queued",
        document_id=doc_id,
        payload={"actor": "tester"},
    )  # type: ignore[arg-type]

    out = jobs_mod.handle_restore_document(qconn, None, job)

    assert out == {"restored": doc_id}
    row = conn.execute(
        "SELECT lifecycle_state, deleted_at, archived_at FROM documents WHERE id=%s",
        (doc_id,),
    ).fetchone()
    assert row["lifecycle_state"] == "active"
    assert row["deleted_at"] is None
    assert row["archived_at"] is None
    history = conn.execute(
        "SELECT field, old_value, new_value, actor FROM document_metadata_history "
        "WHERE document_id=%s ORDER BY id",
        (doc_id,),
    ).fetchall()
    assert [(r["field"], r["old_value"], r["new_value"]) for r in history][-1:] == [
        ("lifecycle_state", "deleted", "active")
    ]
    assert history[-1]["actor"] == "tester"


def test_handle_add_document_tags_merges_tags_and_records_history(qconn, pg):
    conn, created = pg
    doc_id = _itdb.make_doc(conn, "To be tagged.")
    created.append(doc_id)
    job = Job(
        id="0" * 26,
        kind=KIND_ADD_DOCUMENT_TAGS,
        status="queued",
        document_id=doc_id,
        payload={"tags": ["reviewed", "ops"], "actor": "bulk-user"},
    )  # type: ignore[arg-type]

    out = jobs_mod.handle_add_document_tags(qconn, None, job)

    assert out == {"changed": ["tags"]}
    tags = {
        r["tag"]
        for r in conn.execute(
            "SELECT t.tag FROM document_tags dt JOIN tags t ON t.id=dt.tag_id "
            "WHERE dt.document_id=%s",
            (doc_id,),
        ).fetchall()
    }
    assert tags == {"reviewed", "ops"}
    history = conn.execute(
        "SELECT field, old_value, new_value, actor FROM document_metadata_history "
        "WHERE document_id=%s ORDER BY id",
        (doc_id,),
    ).fetchall()
    assert [(r["field"], r["old_value"], r["new_value"], r["actor"]) for r in history] == [
        ("tags", "", "ops, reviewed", "bulk-user")
    ]


def test_handle_suggest_document_metadata_persists_pending_suggestions(qconn, pg):
    conn, created = pg
    doc_id = _itdb.make_doc(conn, "Knowledge graph notes.")
    created.append(doc_id)
    conn.execute(
        "UPDATE documents SET title='upload', original_filename='knowledge-graph-notes.pdf' "
        "WHERE id=%s",
        (doc_id,),
    )
    job = Job(
        id="0" * 26,
        kind=KIND_SUGGEST_DOCUMENT_METADATA,
        status="queued",
        document_id=doc_id,
    )  # type: ignore[arg-type]

    out = jobs_mod.handle_suggest_document_metadata(qconn, None, job)

    assert out["suggested"] >= 1
    row = conn.execute(
        "SELECT field, status FROM document_metadata_suggestion WHERE document_id=%s",
        (doc_id,),
    ).fetchone()
    assert row["field"] == "title"
    assert row["status"] == "pending"


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


def test_handle_update_metadata_replaces_authors_in_order_and_records_history(qconn, pg):
    conn, created = pg
    doc_id = _itdb.make_doc(conn, "Some text.")
    created.append(doc_id)
    job = Job(
        id="0" * 26,
        kind=KIND_UPDATE_METADATA,
        status="queued",
        document_id=doc_id,
        # duplicate + blank are dropped; order is preserved (author order is meaningful)
        payload={"authors": ["Brian Herbert", "Kevin J. Anderson", "  ", "Brian Herbert"]},
    )  # type: ignore[arg-type]

    out = jobs_mod.handle_update_metadata(qconn, None, job)

    assert out["changed"] == ["authors"]
    authors = [
        r["name"]
        for r in conn.execute(
            "SELECT a.name FROM document_authors da JOIN authors a ON a.id=da.author_id "
            "WHERE da.document_id=%s ORDER BY da.position",
            (doc_id,),
        ).fetchall()
    ]
    assert authors == ["Brian Herbert", "Kevin J. Anderson"]
    history = conn.execute(
        "SELECT field, new_value FROM document_metadata_history WHERE document_id=%s",
        (doc_id,),
    ).fetchone()
    assert history["field"] == "authors"
    assert history["new_value"] == "Brian Herbert, Kevin J. Anderson"


def test_handle_update_metadata_can_clear_authors(qconn, pg):
    conn, created = pg
    doc_id = _itdb.make_doc(conn, "Some text.")
    created.append(doc_id)
    jobs_mod.handle_update_metadata(
        qconn,
        None,
        Job(
            id="0" * 26,
            kind=KIND_UPDATE_METADATA,
            status="queued",
            document_id=doc_id,
            payload={"authors": ["A. I. Holmes"]},
        ),  # type: ignore[arg-type]
    )
    out = jobs_mod.handle_update_metadata(
        qconn,
        None,
        Job(
            id="1" * 26,
            kind=KIND_UPDATE_METADATA,
            status="queued",
            document_id=doc_id,
            payload={"authors": []},
        ),  # type: ignore[arg-type]
    )
    assert out["changed"] == ["authors"]
    remaining = conn.execute(
        "SELECT count(*) AS n FROM document_authors WHERE document_id=%s", (doc_id,)
    ).fetchone()
    assert remaining["n"] == 0


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


def test_run_once_ingest_rejects_blob_with_wrong_sha256(qconn):
    import hashlib

    expected = b"expected upload bytes"
    corrupt = b"corrupt upload bytes"
    expected_sha = hashlib.sha256(expected).hexdigest()
    key = f"{expected_sha[:2]}/{expected_sha}.txt"
    store = _BlobStore()
    store.blobs[key] = corrupt
    job_id = queue.enqueue(
        qconn,
        "ingest",
        {
            "storage_key": key,
            "sha256": expected_sha,
            "extension": ".txt",
            "original_filename": "corrupt.txt",
        },
        max_attempts=1,
    )

    jobs_mod.run_once(qconn, store, worker_id="t-integrity", handlers=jobs_mod.HANDLERS)

    row = qconn.execute("SELECT status, error FROM jobs WHERE id = %s", (job_id,)).fetchone()
    assert row["status"] == "failed"
    assert "sha256 mismatch" in row["error"]


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


def test_handle_ingest_threads_the_payload_owner_to_the_pipeline(qconn, monkeypatch):
    """The uploader in the payload reaches ``ingest_file`` unchanged (migration 102)."""
    from doc_ray.application import pipeline as pipeline_mod

    seen: dict[str, object] = {}

    def _fake_ingest_file(_conn, _store, path, owner_principal_id=None):
        seen["owner"] = owner_principal_id
        return pipeline_mod.IngestResult(str(path), None, "duplicate")

    monkeypatch.setattr(pipeline_mod, "ingest_file", _fake_ingest_file)
    store = _BlobStore()
    key = _seed_blob(store, b"Owned upload body.", ".txt")
    job = Job(
        id="0" * 26,
        kind="ingest",
        status="queued",  # type: ignore[arg-type]
        payload={
            "storage_key": key,
            "extension": ".txt",
            "original_filename": "owned.txt",
            "owner_principal_id": "11111111-1111-1111-1111-111111111111",
        },
    )
    jobs_mod.handle_ingest(qconn, store, job)
    assert seen["owner"] == "11111111-1111-1111-1111-111111111111"


def test_handle_ingest_without_an_owner_passes_none_not_the_string_none(qconn, monkeypatch):
    """An ownerless payload must yield ``None``, never the text ``"None"``.

    The handler coerces with ``str(owner) if owner else None``; dropping that
    guard would stringify a missing owner and Postgres would reject (or worse,
    a looser column would store) a literal ``"None"`` as the owning principal.
    Operator CLI ingest legitimately produces ownerless documents, so this path
    is normal traffic, not an edge case.
    """
    from doc_ray.application import pipeline as pipeline_mod

    seen: dict[str, object] = {}

    def _fake_ingest_file(_conn, _store, path, owner_principal_id=None):
        seen["owner"] = owner_principal_id
        return pipeline_mod.IngestResult(str(path), None, "duplicate")

    monkeypatch.setattr(pipeline_mod, "ingest_file", _fake_ingest_file)
    store = _BlobStore()
    key = _seed_blob(store, b"Ownerless upload body.", ".txt")
    job = Job(
        id="0" * 26,
        kind="ingest",
        status="queued",  # type: ignore[arg-type]
        payload={"storage_key": key, "extension": ".txt", "original_filename": "cli.txt"},
    )
    jobs_mod.handle_ingest(qconn, store, job)
    assert seen["owner"] is None


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
