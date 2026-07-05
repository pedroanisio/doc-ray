"""Pipeline-chain and per-kind handler dispatch tests (skip without a DB).

Split out of test_worker.py (which stays focused on run_once's core dispatch
contract) once it grew past the net-LOC gate. Covers: chain-in-payload
advancement (_advance_chain's guards), and the seed_canonical / seed_suggest /
seed_approved / canonicalize / backup / restore handlers' own branches —
network/LLM/DB-writing collaborators are stubbed throughout.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from doc_ray.adapters import queue_postgres as q
from doc_ray.application.jobs import run_once
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


class _NoopKb:
    """Stand-in for the KB clients' ``.close()`` contract (no network, no-op teardown)."""

    def close(self):
        return None


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


def test_handle_seed_canonical_with_only_qids(qconn, monkeypatch):
    conn, created = qconn
    from doc_ray.application.jobs import HANDLERS

    calls = []
    monkeypatch.setattr("doc_ray.adapters.wikidata.WikidataClient", lambda: _NoopKb())
    monkeypatch.setattr(
        "doc_ray.application.canonical_seed.seed_from_ids",
        lambda _c, _kb, ids: calls.append(("ids", ids)) or [1],
    )
    monkeypatch.setattr(
        "doc_ray.application.canonical_seed.seed_from_terms",
        lambda *_a: calls.append(("terms", "should not be called")) or [],
    )
    job_id = q.enqueue(conn, "seed_canonical", {"qids": ["Q1"]}, priority=0)
    created.append(job_id)
    run_once(conn, None, worker_id="w1", handlers=HANDLERS)
    assert q.get(conn, job_id).status is JobStatus.SUCCEEDED
    assert calls == [("ids", ["Q1"])]  # terms branch never entered


def test_handle_seed_canonical_with_only_terms(qconn, monkeypatch):
    conn, created = qconn
    from doc_ray.application.jobs import HANDLERS

    calls = []
    monkeypatch.setattr("doc_ray.adapters.wikidata.WikidataClient", lambda: _NoopKb())
    monkeypatch.setattr(
        "doc_ray.application.canonical_seed.seed_from_ids",
        lambda *_a: calls.append(("ids", "should not be called")) or [],
    )
    monkeypatch.setattr(
        "doc_ray.application.canonical_seed.seed_from_terms",
        lambda _c, _kb, terms: calls.append(("terms", terms)) or [2],
    )
    job_id = q.enqueue(conn, "seed_canonical", {"terms": ["gutenberg"]}, priority=0)
    created.append(job_id)
    run_once(conn, None, worker_id="w1", handlers=HANDLERS)
    assert q.get(conn, job_id).status is JobStatus.SUCCEEDED
    assert calls == [("terms", ["gutenberg"])]  # ids branch never entered


def test_handle_seed_suggest_requires_document_id():
    from doc_ray.application.jobs import handle_seed_suggest
    from doc_ray.domain.jobs import Job, JobStatus

    job = Job(id="x", kind="seed_suggest", status=JobStatus.RUNNING, document_id=None)
    with pytest.raises(RuntimeError, match="seed_suggest requires a document_id"):
        handle_seed_suggest(None, None, job)


def test_handle_seed_approved_requires_document_id():
    from doc_ray.application.jobs import handle_seed_approved
    from doc_ray.domain.jobs import Job, JobStatus

    job = Job(id="x", kind="seed_approved", status=JobStatus.RUNNING, document_id=None)
    with pytest.raises(RuntimeError, match="seed_approved requires a document_id"):
        handle_seed_approved(None, None, job)


def test_handle_canonicalize_requires_document_id():
    from doc_ray.application.jobs import handle_canonicalize
    from doc_ray.domain.jobs import Job, JobStatus

    job = Job(id="x", kind="canonicalize", status=JobStatus.RUNNING, document_id=None)
    with pytest.raises(RuntimeError, match="canonicalize requires a document_id"):
        handle_canonicalize(None, None, job)


def test_chain_stops_when_ingest_result_status_is_not_ingested(qconn, monkeypatch):
    # A 'duplicate' or 'failed' ingest must not advance the chain — otherwise a
    # re-uploaded file or a failed extraction would re-run annotate/canonicalize
    # on nothing new. The handler still returns a real (existing/failed-row)
    # document id in both cases, so use a syntactically valid UUID here too —
    # only the chain-advance guard, not the (legitimate) document_id backfill,
    # is under test. Spies on enqueue rather than scanning the shared jobs
    # table (a live worker may run concurrently against the same DB).
    import _itdb

    conn, created = qconn
    doc_id = _itdb.make_doc(conn, "Pre-existing document. Second sentence.")

    def fake_ingest(_c, _s, _job):
        return {"document_id": doc_id, "status": "duplicate"}

    job_id = q.enqueue(conn, "ingest", {"chain": ["annotate"]}, priority=0)
    created.append(job_id)
    calls = []
    real_enqueue = q.enqueue
    monkeypatch.setattr(
        "doc_ray.adapters.queue_postgres.enqueue",
        lambda *a, **kw: calls.append((a, kw)) or real_enqueue(*a, **kw),
    )
    try:
        run_once(conn, None, worker_id="w1", handlers={"ingest": fake_ingest})
        assert q.get(conn, job_id).status is JobStatus.SUCCEEDED
        assert calls == []  # no 'annotate' successor enqueued
    finally:
        conn.execute("DELETE FROM documents WHERE id = %s", (doc_id,))


def test_chain_stops_when_no_document_id_is_available(qconn):
    # A non-ingest job with a chain but no document_id anywhere (its own row is
    # NULL and its result carries none either) must not advance — there is
    # nothing to chain the next step onto.
    conn, created = qconn
    job_id = q.enqueue(conn, "step-a", {"chain": ["step-b-unreachable"]}, priority=0)
    created.append(job_id)
    run_once(conn, None, worker_id="w1", handlers={"step-a": lambda c, s, j: {"ok": 1}})
    assert q.get(conn, job_id).status is JobStatus.SUCCEEDED
    nxt = conn.execute("SELECT 1 FROM jobs WHERE kind = 'step-b-unreachable'").fetchone()
    assert nxt is None


def test_chain_stops_when_chain_is_empty(qconn, monkeypatch):
    # Spy on enqueue rather than inspect the shared jobs table (a live worker
    # may be running concurrently against the same DB).
    conn, created = qconn
    job_id = q.enqueue(conn, "step-a", {}, priority=0)  # no 'chain' key at all
    created.append(job_id)

    calls = []
    real_enqueue = q.enqueue
    monkeypatch.setattr(
        "doc_ray.adapters.queue_postgres.enqueue",
        lambda *a, **kw: calls.append((a, kw)) or real_enqueue(*a, **kw),
    )
    run_once(conn, None, worker_id="w1", handlers={"step-a": lambda c, s, j: {"ok": 1}})
    assert calls == []  # no successor enqueued


def test_handle_seed_canonical_dispatches_qids_and_terms(qconn, monkeypatch):
    conn, created = qconn
    from doc_ray.application.jobs import HANDLERS

    calls: list[tuple[str, list]] = []
    monkeypatch.setattr("doc_ray.adapters.wikidata.WikidataClient", lambda: _NoopKb())
    monkeypatch.setattr(
        "doc_ray.application.canonical_seed.seed_from_ids",
        lambda _c, _kb, ids: calls.append(("ids", ids)) or [1],
    )
    monkeypatch.setattr(
        "doc_ray.application.canonical_seed.seed_from_terms",
        lambda _c, _kb, terms: calls.append(("terms", terms)) or [2, 3],
    )
    job_id = q.enqueue(conn, "seed_canonical", {"qids": ["Q1"], "terms": ["gutenberg"]}, priority=0)
    created.append(job_id)
    run_once(conn, None, worker_id="w1", handlers=HANDLERS)
    assert q.get(conn, job_id).status is JobStatus.SUCCEEDED
    result = conn.execute("SELECT result FROM jobs WHERE id = %s", (job_id,)).fetchone()["result"]
    assert result == {"seeded": 3}  # 1 from ids + 2 from terms
    assert ("ids", ["Q1"]) in calls and ("terms", ["gutenberg"]) in calls


def test_handle_seed_suggest_dispatches_to_service(qconn, monkeypatch):
    import _itdb

    conn, created = qconn
    from doc_ray.application.jobs import HANDLERS

    doc_id = _itdb.make_doc(conn, "Suggest me a seed. Second sentence.")
    seen = {}
    monkeypatch.setattr("doc_ray.adapters.wikidata.WikidataClient", lambda: _NoopKb())
    monkeypatch.setattr("doc_ray.adapters.wikipedia.WikipediaClient", lambda: _NoopKb())
    monkeypatch.setattr(
        "doc_ray.application.seed_suggest.suggest_for_document",
        lambda _c, _wiki, _kb, did, **kw: (
            seen.update(document_id=did, **kw) or {"suggested": 5, "resolved": 2}
        ),
    )
    job_id = q.enqueue(
        conn,
        "seed_suggest",
        {"limit": 10, "min_mentions": 1, "min_lexemes": 2},
        priority=0,
        document_id=doc_id,
    )
    created.append(job_id)
    try:
        run_once(conn, None, worker_id="w1", handlers=HANDLERS)
        assert q.get(conn, job_id).status is JobStatus.SUCCEEDED
        result = conn.execute("SELECT result FROM jobs WHERE id = %s", (job_id,)).fetchone()[
            "result"
        ]
        assert result == {"suggested": 5, "resolved": 2}
        assert seen == {"document_id": doc_id, "limit": 10, "min_mentions": 1, "min_lexemes": 2}
    finally:
        conn.execute("DELETE FROM documents WHERE id = %s", (doc_id,))


def test_handle_seed_approved_with_no_approved_rows_seeds_nothing(qconn):
    import _itdb

    conn, created = qconn
    from doc_ray.application.jobs import HANDLERS

    doc_id = _itdb.make_doc(conn, "Nothing approved yet. Second sentence.")
    job_id = q.enqueue(conn, "seed_approved", {}, priority=0, document_id=doc_id)
    created.append(job_id)
    try:
        run_once(conn, None, worker_id="w1", handlers=HANDLERS)
        assert q.get(conn, job_id).status is JobStatus.SUCCEEDED
        result = conn.execute("SELECT result FROM jobs WHERE id = %s", (job_id,)).fetchone()[
            "result"
        ]
        assert result == {"seeded": 0}
    finally:
        conn.execute("DELETE FROM documents WHERE id = %s", (doc_id,))


def test_handle_canonicalize_resolve_true_invokes_frontier_resolver(qconn, monkeypatch):
    # payload {"resolve": true} must run Stage 3 on the Stage-2 residue — stub
    # the resolver so this never makes a real Anthropic call. The residue is
    # made deterministic (a synthetic unlinked mention) rather than relying on
    # spaCy's NER to tag a nonsense word.
    import _itdb

    from doc_ray.application.resolution import ResolutionOutcome
    from doc_ray.domain.ulid import new_ulid_str

    conn, created = qconn
    from doc_ray.application.jobs import HANDLERS

    text = "Zzqxlorph appeared once. Second sentence."
    doc_id = _itdb.make_doc(conn, text)
    ann = new_ulid_str()
    conn.execute(
        "INSERT INTO annotations (id, document_id, layer, tool) VALUES (%s,%s,'spacy','test')",
        (ann, doc_id),
    )
    span = conn.execute(
        "INSERT INTO spans (document_id, char_start, char_end) VALUES (%s, 0, 10) RETURNING id",
        (doc_id,),
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO entity_mention (id, annotation_id, span_id, entity_type) "
        "VALUES (%s,%s,%s,'ORG')",
        (new_ulid_str(), ann, span),
    )
    seen = {}
    monkeypatch.setattr("doc_ray.adapters.resolver_claude.ClaudeResolver", lambda: "stub-resolver")
    monkeypatch.setattr(
        "doc_ray.application.resolution.resolve_residue",
        lambda _c, resolver, did, residue: (
            seen.update(resolver=resolver, document_id=did, n_residue=len(residue))
            or ResolutionOutcome(linked=1, created=2, review=3, skipped=0)
        ),
    )
    job_id = q.enqueue(conn, "canonicalize", {"resolve": True}, priority=0, document_id=doc_id)
    created.append(job_id)
    try:
        run_once(conn, None, worker_id="w1", handlers=HANDLERS)
        assert q.get(conn, job_id).status is JobStatus.SUCCEEDED
        result = conn.execute("SELECT result FROM jobs WHERE id = %s", (job_id,)).fetchone()[
            "result"
        ]
        assert result["resolved"] == 3  # linked + created
        assert result["created"] == 2 and result["review"] == 3
        assert seen["resolver"] == "stub-resolver" and seen["document_id"] == doc_id
    finally:
        conn.execute("DELETE FROM documents WHERE id = %s", (doc_id,))


def test_handle_canonicalize_resolve_true_with_no_residue_skips_resolver(qconn, monkeypatch):
    # resolve=True with an empty residue must not construct a resolver at all
    # (no Anthropic client, no network) — the guard is `and result.residue`.
    import _itdb

    conn, created = qconn
    from doc_ray.application.jobs import HANDLERS

    doc_id = _itdb.make_doc(conn, "Nothing ambiguous here at all whatsoever.")

    def _boom():
        raise AssertionError("ClaudeResolver must not be constructed with empty residue")

    monkeypatch.setattr("doc_ray.adapters.resolver_claude.ClaudeResolver", _boom)
    job_id = q.enqueue(conn, "canonicalize", {"resolve": True}, priority=0, document_id=doc_id)
    created.append(job_id)
    try:
        run_once(conn, None, worker_id="w1", handlers=HANDLERS)
        assert q.get(conn, job_id).status is JobStatus.SUCCEEDED
    finally:
        conn.execute("DELETE FROM documents WHERE id = %s", (doc_id,))


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
