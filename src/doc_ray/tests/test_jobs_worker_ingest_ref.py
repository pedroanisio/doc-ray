"""``handle_ingest``'s remote-reference route (D-015 split from the handler suite).

The upload route is covered in ``test_jobs_worker_handlers.py``; this file covers
the branch that resolves a reference against the source registry, fetches it, and
ingests the bytes — including the title precedence and the failure modes that must
raise so the queue records them.
"""

from __future__ import annotations

import _itdb
import pytest

from doc_ray.application import jobs as jobs_mod
from doc_ray.domain.jobs import Job

# `qconn` comes from conftest; only the store double is shared with the upload suite.
from doc_ray.tests.test_jobs_worker_handlers import _BlobStore


class _FakeSource:
    """A DocumentSource double that yields fixed bytes for any ref."""

    name = "fake"

    def __init__(self, fetched=None, exc: Exception | None = None) -> None:
        self._fetched = fetched
        self._exc = exc
        self.seen: list[str] = []

    def matches(self, ref: str) -> bool:
        return True

    def fetch(self, ref: str):
        self.seen.append(ref)
        if self._exc is not None:
            raise self._exc
        return self._fetched


def _ref_job(ref: str, **payload) -> Job:
    return Job(
        id="0" * 26,
        kind="ingest",
        status="queued",  # type: ignore[arg-type]
        payload={"ref": ref, **payload},
    )


def test_handle_ingest_fetches_a_reference_through_the_source_registry(qconn, monkeypatch):
    """A ref payload resolves, fetches, and ingests with the source's title + URL.

    The worker owns the download so the API can answer immediately; this is the
    same path ``ingest_refs`` takes for the CLI, expressed as one queued job.
    """
    from doc_ray.adapters import document_source as source_mod
    from doc_ray.adapters.document_source import Fetched

    marker = _itdb.unique_sha()[:8]
    fetched = Fetched(
        data=f"Referenced alpha {marker}. Referenced beta.".encode(),
        filename=f"ref-{marker}.txt",
        source_url="https://arxiv.org/pdf/2401.00001",
        title="Fetched By Worker",
    )
    source = _FakeSource(fetched)
    monkeypatch.setattr(source_mod, "resolve_source", lambda _ref: source)

    out = jobs_mod.handle_ingest(qconn, _BlobStore(), _ref_job("arxiv:2401.00001"))

    assert out["status"] == "ingested"
    assert source.seen == ["arxiv:2401.00001"]
    try:
        row = qconn.execute(
            "SELECT d.title, m.value AS src FROM documents d "
            "LEFT JOIN document_metadata m ON m.document_id = d.id AND m.key = 'source_url' "
            "WHERE d.id = %s",
            (out["document_id"],),
        ).fetchone()
        assert row["title"] == "Fetched By Worker"
        assert row["src"] == "https://arxiv.org/pdf/2401.00001"
    finally:
        qconn.execute("DELETE FROM documents WHERE id = %s", (out["document_id"],))


def test_handle_ingest_reference_threads_the_payload_owner(qconn, monkeypatch):
    """A referenced ingest lands in the requesting principal's corpus (migration 102)."""
    from doc_ray.application import pipeline as pipeline_mod

    seen: dict[str, object] = {}

    def _fake_ingest_bytes(_conn, _store, _data, *, filename, owner_principal_id=None, **_kw):
        seen["owner"] = owner_principal_id
        return pipeline_mod.IngestResult(filename, None, "duplicate")

    monkeypatch.setattr(pipeline_mod, "ingest_bytes", _fake_ingest_bytes)
    from doc_ray.adapters import document_source as source_mod
    from doc_ray.adapters.document_source import Fetched

    monkeypatch.setattr(
        source_mod,
        "resolve_source",
        lambda _ref: _FakeSource(Fetched(b"bytes", "r.txt", "https://example.org/r")),
    )

    jobs_mod.handle_ingest(
        qconn,
        _BlobStore(),
        _ref_job("arxiv:2401.00004", owner_principal_id="11111111-1111-1111-1111-111111111111"),
    )

    assert seen["owner"] == "11111111-1111-1111-1111-111111111111"


def test_handle_ingest_reference_fetch_failure_raises_so_the_job_records_it(qconn, monkeypatch):
    """A fetch that fails must fail the job, not report a phantom success.

    There are no bytes to keep for forensics, so unlike an extraction failure
    this leaves no document row — the queue's retry/error record is the trail.
    """
    from doc_ray.adapters import document_source as source_mod

    monkeypatch.setattr(
        source_mod, "resolve_source", lambda _ref: _FakeSource(exc=RuntimeError("net down"))
    )

    with pytest.raises(RuntimeError, match="net down"):
        jobs_mod.handle_ingest(qconn, _BlobStore(), _ref_job("arxiv:2401.00005"))


def test_handle_ingest_reference_without_a_source_adapter_raises(qconn, monkeypatch):
    """A ref nothing claims is a job error naming the problem."""
    from doc_ray.adapters import document_source as source_mod

    monkeypatch.setattr(source_mod, "resolve_source", lambda _ref: None)

    with pytest.raises(RuntimeError, match="no source adapter"):
        jobs_mod.handle_ingest(qconn, _BlobStore(), _ref_job("https://example.org/x.pdf"))


def test_handle_ingest_still_requires_a_storage_key_when_no_ref_is_given(qconn):
    """The upload route is unchanged: no ref means the payload must carry a blob."""
    job = Job(id="0" * 26, kind="ingest", status="queued", payload={})  # type: ignore[arg-type]

    with pytest.raises((KeyError, RuntimeError)):
        jobs_mod.handle_ingest(qconn, _BlobStore(), job)


def test_handle_ingest_reference_lets_an_explicit_title_beat_the_source_title(qconn, monkeypatch):
    """Title precedence: caller > source (arXiv Atom) > extractor.

    The caller asked for a specific title; the source's is a better default than
    the extractor's, but it is still a default.
    """
    from doc_ray.adapters import document_source as source_mod
    from doc_ray.adapters.document_source import Fetched
    from doc_ray.application import pipeline as pipeline_mod

    seen: dict[str, object] = {}

    def _fake_ingest_bytes(_conn, _store, _data, *, filename, title=None, **_kw):
        seen["title"] = title
        return pipeline_mod.IngestResult(filename, None, "duplicate")

    monkeypatch.setattr(pipeline_mod, "ingest_bytes", _fake_ingest_bytes)
    monkeypatch.setattr(
        source_mod,
        "resolve_source",
        lambda _ref: _FakeSource(
            Fetched(b"bytes", "r.txt", "https://example.org/r", title="Source Title")
        ),
    )

    jobs_mod.handle_ingest(qconn, _BlobStore(), _ref_job("arxiv:2401.00006", title="Caller Title"))

    assert seen["title"] == "Caller Title"


def test_handle_ingest_reference_falls_back_to_the_source_title(qconn, monkeypatch):
    """With no caller title, the source's title wins over the extractor's."""
    from doc_ray.adapters import document_source as source_mod
    from doc_ray.adapters.document_source import Fetched
    from doc_ray.application import pipeline as pipeline_mod

    seen: dict[str, object] = {}

    def _fake_ingest_bytes(_conn, _store, _data, *, filename, title=None, **_kw):
        seen["title"] = title
        return pipeline_mod.IngestResult(filename, None, "duplicate")

    monkeypatch.setattr(pipeline_mod, "ingest_bytes", _fake_ingest_bytes)
    monkeypatch.setattr(
        source_mod,
        "resolve_source",
        lambda _ref: _FakeSource(
            Fetched(b"bytes", "r.txt", "https://example.org/r", title="Source Title")
        ),
    )

    jobs_mod.handle_ingest(qconn, _BlobStore(), _ref_job("arxiv:2401.00007"))

    assert seen["title"] == "Source Title"


def test_handle_ingest_reference_reports_a_duplicate_without_reprocessing(qconn, monkeypatch):
    """Re-referencing content already in the corpus dedupes on the SHA like any intake.

    The chain guard keys off this status, so a re-referenced document must not
    re-enter the pipeline.
    """
    from doc_ray.adapters import document_source as source_mod
    from doc_ray.adapters.document_source import Fetched

    marker = _itdb.unique_sha()[:8]
    fetched = Fetched(
        data=f"Twice referenced {marker}. Second sentence.".encode(),
        filename=f"twice-{marker}.txt",
        source_url="https://arxiv.org/pdf/2401.00008",
    )
    monkeypatch.setattr(source_mod, "resolve_source", lambda _ref: _FakeSource(fetched))
    store = _BlobStore()

    first = jobs_mod.handle_ingest(qconn, store, _ref_job("arxiv:2401.00008"))
    try:
        again = jobs_mod.handle_ingest(qconn, store, _ref_job("arxiv:2401.00008"))
        assert first["status"] == "ingested"
        assert again["status"] == "duplicate"
        assert again["document_id"] == first["document_id"]
    finally:
        qconn.execute("DELETE FROM documents WHERE id = %s", (first["document_id"],))
