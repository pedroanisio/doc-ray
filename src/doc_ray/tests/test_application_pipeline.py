"""Ingest pipeline: hash → store blob → extract → persist (with a fake store)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import _itdb
import pytest

from doc_ray.application import pipeline
from doc_ray.config import Config
from doc_ray.domain import progress as domain_progress


class FakeStore:
    """In-memory ObjectStore double (put/get/bucket/ensure_bucket)."""

    def __init__(self, cfg: Config | None = None) -> None:
        self.bucket = "test-bucket"
        self.blobs: dict[str, bytes] = {}

    def ensure_bucket(self) -> None:
        return None

    def put(self, key: str, data: bytes, content_type: str) -> str:
        self.blobs[key] = data
        return f"s3://{self.bucket}/{key}"

    def get(self, key: str) -> bytes:
        return self.blobs[key]


@pytest.fixture()
def store() -> FakeStore:
    return FakeStore()


def _txt(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_ingest_file_txt_roundtrip(pg, store, tmp_path):
    conn, created = pg
    path = _txt(tmp_path, f"it-{_itdb.unique_sha()[:8]}.txt", "One sentence. Two sentences.")

    r = pipeline.ingest_file(conn, store, path)
    created.append(r.document_id)
    assert r.status == "ingested" and r.document_id
    assert "2 sentences" in r.detail
    assert len(store.blobs) == 1  # blob landed content-addressed

    row = conn.execute(
        "SELECT status, char_count, text_sha256 FROM documents WHERE id=%s", (r.document_id,)
    ).fetchone()
    assert row["status"] == "extracted" and row["char_count"] > 0
    n = conn.execute(
        "SELECT count(*) AS n FROM sentences WHERE document_id=%s", (r.document_id,)
    ).fetchone()["n"]
    assert n == 2


def test_ingest_file_duplicate_is_noop(pg, store, tmp_path):
    conn, created = pg
    path = _txt(tmp_path, f"dup-{_itdb.unique_sha()[:8]}.txt", "Same bytes twice.")
    first = pipeline.ingest_file(conn, store, path)
    created.append(first.document_id)
    again = pipeline.ingest_file(conn, store, path)
    assert again.status == "duplicate"
    assert again.document_id == first.document_id


def test_reingesting_previously_failed_bytes_reports_failed_not_duplicate(pg, store, tmp_path):
    # Regression: the SHA dedup returns the *failed* document row written by the
    # first attempt, so extraction can never re-run for these bytes. Calling that
    # a "duplicate" turned a permanent failure into a benign-looking no-op — the
    # Spaces loader recorded it as terminal-OK and exited 0 while the document
    # sat in status='failed'. The second attempt must report the failure, carry
    # the recorded error, and point at the same document row.
    path = _txt(tmp_path, f"nofail-{_itdb.unique_sha()[:8]}.txt", "\x00\x00 \x00\n")

    conn, created = pg
    first = pipeline.ingest_file(conn, store, path)
    created.append(first.document_id)
    again = pipeline.ingest_file(conn, store, path)

    assert first.status == "failed"
    assert again.status == "failed"
    assert again.document_id == first.document_id
    assert "no extractable text" in (again.detail or "")


def test_finalize_new_document_refreshes_stats_before_chaining(pg, tmp_path, store):
    # Regression: the rollup refresh and the chain enqueue are one indivisible
    # obligation. A caller that enqueued the chain but skipped the refresh (the
    # Spaces loader, pre-fix) left a fresh document reading zero sentences until
    # some later job happened to refresh it — and forever under --no-pipeline.
    conn, created = pg
    path = _txt(tmp_path, f"fin-{_itdb.unique_sha()[:8]}.txt", "Alpha beta. Gamma delta.")
    result = pipeline.ingest_file(conn, store, path)
    created.append(result.document_id)

    try:
        job_id = pipeline.finalize_new_document(conn, result, ("annotate",))

        assert job_id is not None
        stats = conn.execute(
            "SELECT n_sentences FROM document_stats WHERE document_id=%s", (result.document_id,)
        ).fetchone()
        assert stats is not None and stats["n_sentences"] == 2
        assert "queued annotate" in result.detail
    finally:
        conn.execute("DELETE FROM jobs WHERE document_id=%s", (result.document_id,))


def test_finalize_new_document_refreshes_stats_even_with_no_chain(pg, tmp_path, store):
    # --no-pipeline must still leave an accurate read-model rollup: the document
    # is complete as text, it simply owes no queue work.
    conn, created = pg
    path = _txt(tmp_path, f"nochain-{_itdb.unique_sha()[:8]}.txt", "Alpha beta. Gamma delta.")
    result = pipeline.ingest_file(conn, store, path)
    created.append(result.document_id)

    assert pipeline.finalize_new_document(conn, result, ()) is None
    stats = conn.execute(
        "SELECT n_sentences FROM document_stats WHERE document_id=%s", (result.document_id,)
    ).fetchone()
    assert stats is not None and stats["n_sentences"] == 2


def test_finalize_new_document_ignores_duplicates_and_failures(pg, tmp_path, store):
    conn, _created = pg
    for status in ("duplicate", "failed"):
        result = pipeline.IngestResult("x", "doc-absent", status)
        assert pipeline.finalize_new_document(conn, result, ("annotate",)) is None
        assert result.detail == ""


def test_ingest_file_extraction_failure_records_failed_doc(pg, store, tmp_path):
    conn, created = pg
    bad = tmp_path / f"bad-{_itdb.unique_sha()[:8]}.zzz"  # unsupported extension
    bad.write_bytes(b"\x00\x01mystery")
    r = pipeline.ingest_file(conn, store, bad)
    created.append(r.document_id)
    assert r.status == "failed" and r.document_id
    row = conn.execute(
        "SELECT status, error FROM documents WHERE id=%s", (r.document_id,)
    ).fetchone()
    assert row["status"] == "failed" and row["error"]
    assert len(store.blobs) == 1  # the blob is still recorded for forensics


def test_ingest_paths_shares_one_store(pg, tmp_path, monkeypatch):
    conn, created = pg
    monkeypatch.setattr(pipeline, "ObjectStore", FakeStore)
    p1 = _txt(tmp_path, f"a-{_itdb.unique_sha()[:8]}.txt", "Alpha.")
    p2 = _txt(tmp_path, f"b-{_itdb.unique_sha()[:8]}.txt", "Beta.")
    results = pipeline.ingest_paths(Config.from_env(), [p1, p2])
    created.extend(r.document_id for r in results if r.document_id)
    assert [r.status for r in results] == ["ingested", "ingested"]


def test_expand_paths_recurses_and_filters(tmp_path):
    (tmp_path / "sub").mkdir()
    keep1 = _txt(tmp_path, "top.txt", "x")
    keep2 = _txt(tmp_path / "sub", "deep.md", "y")
    _txt(tmp_path / "sub", "skip.bin", "z")
    got = pipeline.expand_paths([str(tmp_path), str(keep1)], {".txt", ".md"})
    assert keep2 in got and got.count(keep1) == 2  # dir hit + explicit file hit
    assert all(p.suffix in {".txt", ".md"} for p in got)


def test_ingest_file_with_no_extractable_text_fails_clearly(pg, store, tmp_path):
    # Live incident: a 368-page scanned PDF (zero text layer) sailed through as
    # an empty 'ingested' document and ran the whole chain on nothing. A file
    # with no extractable text must record a FAILED document with a message
    # that names the remedy — and (via the chain's status guard) never
    # advance the pipeline.
    path = _txt(tmp_path, f"it-{_itdb.unique_sha()[:8]}.txt", "\x00\x00 \x00\n")

    r = pipeline.ingest_file(conn := pg[0], store, path)
    pg[1].append(r.document_id)
    assert r.status == "failed"
    assert "no extractable text" in (r.detail or "")
    row = conn.execute(
        "SELECT status, error FROM documents WHERE id = %s", (r.document_id,)
    ).fetchone()
    assert row["status"] == "failed"
    assert "OCR" in row["error"]


def test_ingest_file_reports_inner_progress_stages(monkeypatch, store, tmp_path):
    path = _txt(tmp_path, "progress.txt", "Alpha. Beta.")
    events: list[tuple[int | None, int | None, str | None]] = []
    token = domain_progress.set_progress_reporter(
        lambda current, total, stage: events.append((current, total, stage))
    )

    monkeypatch.setattr(pipeline.db, "find_by_sha256", lambda *_args: None)
    monkeypatch.setattr(
        pipeline.extract_mod,
        "extract",
        lambda *_args, **_kwargs: SimpleNamespace(
            doc_type="text",
            title="progress",
            author=None,
            page_count=None,
            full_text="Alpha. Beta.",
            content_type="text/plain",
            extractor="fake",
            extractor_version="1",
            metadata={},
            page_runs=None,
        ),
    )
    monkeypatch.setattr(
        pipeline.db,
        "insert_document",
        lambda *_args, **_kwargs: "doc-progress",
    )

    try:
        result = pipeline.ingest_file(object(), store, path)  # type: ignore[arg-type]
    finally:
        domain_progress.reset_progress_reporter(token)

    assert result.document_id == "doc-progress"
    assert events == [
        (0, 5, "Reading file"),
        (1, 5, "Checking duplicate content"),
        (2, 5, "Extracting document text"),
        (3, 5, "Storing original blob"),
        (4, 5, "Persisting extracted document"),
    ]


def _extracted(full_text: str) -> SimpleNamespace:
    """Minimal extractor result the persist path accepts."""
    return SimpleNamespace(
        doc_type="text",
        title="owned",
        author=None,
        page_count=None,
        full_text=full_text,
        content_type="text/plain",
        extractor="fake",
        extractor_version="1",
        metadata={},
        page_runs=None,
    )


def test_ingest_file_stamps_the_owner_on_the_success_path(tmp_path, monkeypatch):
    """A caller-supplied owner reaches insert_document (migration 102)."""
    path = tmp_path / "owned.txt"
    path.write_text("Alpha. Beta.", encoding="utf-8")
    captured: dict[str, object] = {}

    monkeypatch.setattr(pipeline.db, "find_by_sha256", lambda *_a: None)
    monkeypatch.setattr(
        pipeline.extract_mod, "extract", lambda *_a, **_k: _extracted("Alpha. Beta.")
    )
    monkeypatch.setattr(
        pipeline.db,
        "insert_document",
        lambda *_a, **kwargs: captured.update(kwargs) or "doc-owned",
    )

    result = pipeline.ingest_file(
        object(),  # type: ignore[arg-type]
        FakeStore(),  # type: ignore[arg-type]
        path,
        owner_principal_id="22222222-2222-2222-2222-222222222222",
    )

    assert result.status == "ingested"
    assert captured["owner_principal_id"] == "22222222-2222-2222-2222-222222222222"


def test_ingest_file_stamps_the_owner_on_the_failure_path(tmp_path, monkeypatch):
    """A failed extraction still records ownership.

    record_failure persists a FAILED document row so the upload is not lost.
    Leaving it unowned would strand the row where its uploader cannot see it —
    the failure would be invisible to the only person who could act on it.
    """
    path = tmp_path / "broken.txt"
    path.write_text("unreadable", encoding="utf-8")
    captured: dict[str, object] = {}

    monkeypatch.setattr(pipeline.db, "find_by_sha256", lambda *_a: None)

    def _boom(*_a, **_k):
        raise RuntimeError("extractor exploded")

    monkeypatch.setattr(pipeline.extract_mod, "extract", _boom)
    monkeypatch.setattr(
        pipeline.db,
        "insert_document",
        lambda *_a, **kwargs: captured.update(kwargs) or "doc-failed",
    )

    result = pipeline.ingest_file(
        object(),  # type: ignore[arg-type]
        FakeStore(),  # type: ignore[arg-type]
        path,
        owner_principal_id="33333333-3333-3333-3333-333333333333",
    )

    assert result.status == "failed"
    assert captured["status"] == "failed"
    assert captured["owner_principal_id"] == "33333333-3333-3333-3333-333333333333"


# --- BL-162: every ingest path owes the document the default pipeline ------------------------
#
# ingest_paths (the CLI / operator bulk path) previously wrote text + sentences
# and stopped: no pipeline chain enqueued, no document_stats refresh. The result
# was the "silent no-layer" state — a document with real sentences whose
# sentenceCount read 0 and whose annotation layers never arrived, with no failed
# job and no error anywhere. DEFAULT_PIPELINE_CHAIN's own docstring names the
# invariant: it is "the pipeline every ingest path owes a document".


def test_ingest_paths_enqueues_the_default_chain_and_refreshes_stats(pg, tmp_path, monkeypatch):
    conn, created = pg
    monkeypatch.setattr(pipeline, "ObjectStore", FakeStore)
    p = _txt(tmp_path, f"chain-{_itdb.unique_sha()[:8]}.txt", "Alpha. Beta.")

    results = pipeline.ingest_paths(Config.from_env(), [p])

    doc_id = results[0].document_id
    created.append(doc_id)
    try:
        assert results[0].status == "ingested"
        assert "queued annotate" in results[0].detail
        job = conn.execute(
            "SELECT kind, payload FROM jobs WHERE document_id=%s ORDER BY id DESC LIMIT 1",
            (doc_id,),
        ).fetchone()
        assert job is not None and job["kind"] == "annotate"
        assert list(job["payload"]["chain"]) == [
            "semantic_enrich",
            "canonicalize",
            "visual_extract",
        ]
        stats = conn.execute(
            "SELECT n_sentences FROM document_stats WHERE document_id=%s", (doc_id,)
        ).fetchone()
        assert stats is not None and stats["n_sentences"] == 2
    finally:
        conn.execute("DELETE FROM jobs WHERE document_id=%s", (doc_id,))


def test_ingest_paths_does_not_rechain_duplicates(pg, tmp_path, monkeypatch):
    conn, created = pg
    monkeypatch.setattr(pipeline, "ObjectStore", FakeStore)
    p = _txt(tmp_path, f"dupch-{_itdb.unique_sha()[:8]}.txt", "Same. Bytes.")

    first = pipeline.ingest_paths(Config.from_env(), [p])[0]
    created.append(first.document_id)
    try:
        again = pipeline.ingest_paths(Config.from_env(), [p])[0]
        assert again.status == "duplicate"
        n = conn.execute(
            "SELECT count(*) AS n FROM jobs WHERE document_id=%s AND kind='annotate'",
            (first.document_id,),
        ).fetchone()["n"]
        assert n == 1  # the duplicate did not re-enter the pipeline
    finally:
        conn.execute("DELETE FROM jobs WHERE document_id=%s", (first.document_id,))


def test_ingest_paths_chain_none_disables_the_pipeline(pg, tmp_path, monkeypatch):
    conn, created = pg
    monkeypatch.setattr(pipeline, "ObjectStore", FakeStore)
    p = _txt(tmp_path, f"noch-{_itdb.unique_sha()[:8]}.txt", "Gamma.")

    r = pipeline.ingest_paths(Config.from_env(), [p], chain=None)[0]

    created.append(r.document_id)
    n = conn.execute(
        "SELECT count(*) AS n FROM jobs WHERE document_id=%s", (r.document_id,)
    ).fetchone()["n"]
    assert n == 0


def test_ingest_file_defaults_to_unowned(tmp_path, monkeypatch):
    """Operator CLI ingest passes no owner and must produce an unowned document."""
    path = tmp_path / "cli.txt"
    path.write_text("Gamma. Delta.", encoding="utf-8")
    captured: dict[str, object] = {}

    monkeypatch.setattr(pipeline.db, "find_by_sha256", lambda *_a: None)
    monkeypatch.setattr(
        pipeline.extract_mod, "extract", lambda *_a, **_k: _extracted("Gamma. Delta.")
    )
    monkeypatch.setattr(
        pipeline.db,
        "insert_document",
        lambda *_a, **kwargs: captured.update(kwargs) or "doc-cli",
    )

    pipeline.ingest_file(object(), FakeStore(), path)  # type: ignore[arg-type]

    assert captured["owner_principal_id"] is None


# --- bytes-first core + source adapters (URL ingest) -----------------------------------------


def test_ingest_bytes_applies_title_and_source_url(pg, store):
    conn, created = pg
    marker = _itdb.unique_sha()[:8]
    r = pipeline.ingest_bytes(
        conn,
        store,
        f"One {marker}. Two.".encode(),
        filename=f"note-{marker}.txt",
        title="Chosen Title",
        source_url="https://arxiv.org/pdf/9999.99999",
    )
    created.append(r.document_id)
    assert r.status == "ingested"
    # Per-document metadata is 4NF-normalised into document_metadata (migration
    # 004); documents.metadata has not existed since.
    row = conn.execute(
        "SELECT d.title, m.value AS src FROM documents d "
        "LEFT JOIN document_metadata m ON m.document_id = d.id AND m.key = 'source_url' "
        "WHERE d.id=%s",
        (r.document_id,),
    ).fetchone()
    assert row["title"] == "Chosen Title"
    assert row["src"] == "https://arxiv.org/pdf/9999.99999"


def test_ingest_file_delegates_to_ingest_bytes(pg, store, tmp_path):
    """The path wrapper and the bytes core must produce identical documents."""
    conn, created = pg
    path = _txt(tmp_path, f"wrap-{_itdb.unique_sha()[:8]}.txt", "Alpha. Beta.")
    r = pipeline.ingest_file(conn, store, path)
    created.append(r.document_id)
    assert r.status == "ingested"
    assert r.path == str(path)  # wrapper keeps the filesystem path as the label


class _FakeSource:
    name = "fake"

    def __init__(self, fetched=None, exc: Exception | None = None) -> None:
        self._fetched = fetched
        self._exc = exc

    def matches(self, ref: str) -> bool:
        return True

    def fetch(self, ref: str):
        if self._exc is not None:
            raise self._exc
        return self._fetched


def test_ingest_refs_fetches_and_persists(pg, monkeypatch):
    from doc_ray.adapters import document_source as source_mod
    from doc_ray.adapters.document_source import Fetched

    conn, created = pg
    marker = _itdb.unique_sha()[:8]
    fetched = Fetched(
        data=f"Ref alpha {marker}. Ref beta.".encode(),
        filename=f"ref-{marker}.txt",
        source_url=f"https://example.org/{marker}",
        title="Fetched Title",
    )
    monkeypatch.setattr(source_mod, "resolve_source", lambda ref: _FakeSource(fetched))
    monkeypatch.setattr(pipeline, "ObjectStore", FakeStore)

    r = pipeline.ingest_refs(Config.from_env(), [f"https://example.org/{marker}"], chain=None)[0]

    created.append(r.document_id)
    assert r.status == "ingested"
    assert r.path == f"https://example.org/{marker}"  # result labeled by the ref
    row = conn.execute(
        "SELECT d.title, m.value AS src FROM documents d "
        "LEFT JOIN document_metadata m ON m.document_id = d.id AND m.key = 'source_url' "
        "WHERE d.id=%s",
        (r.document_id,),
    ).fetchone()
    assert row["title"] == "Fetched Title"
    assert row["src"] == f"https://example.org/{marker}"


def test_ingest_refs_records_fetch_failure_without_a_row(pg, monkeypatch):
    from doc_ray.adapters import document_source as source_mod

    monkeypatch.setattr(
        source_mod, "resolve_source", lambda ref: _FakeSource(exc=RuntimeError("net down"))
    )
    monkeypatch.setattr(pipeline, "ObjectStore", FakeStore)

    r = pipeline.ingest_refs(Config.from_env(), ["https://example.org/x"], chain=None)[0]

    assert r.status == "failed" and r.document_id is None
    assert "net down" in r.detail


def test_ingest_refs_rejects_unresolvable_ref(pg, monkeypatch):
    from doc_ray.adapters import document_source as source_mod

    monkeypatch.setattr(source_mod, "resolve_source", lambda ref: None)
    monkeypatch.setattr(pipeline, "ObjectStore", FakeStore)

    r = pipeline.ingest_refs(Config.from_env(), ["https://nowhere.example/x"], chain=None)[0]

    assert r.status == "failed" and r.document_id is None
    assert "no source adapter" in r.detail
