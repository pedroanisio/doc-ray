"""Unit tests for the pure logic of the Spaces ingest loader (scripts/ingest_from_spaces.py).

These cover the idempotency decision, extension filtering, manifest round-trip,
post-ingest pipeline chaining, and owner resolution without touching the network,
the database, or the object store.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import ingest_from_spaces as loader
import pytest


def _obj(key: str, etag: str = "abc", size: int = 10) -> loader.SourceObject:
    return loader.SourceObject(key=key, etag=etag, size=size)


def test_decide_action_new_object_ingests() -> None:
    assert loader.decide_action(_obj("a.pdf"), None, force=False) == "ingest"


def test_decide_action_unchanged_terminal_skips() -> None:
    for status in ("ingested", "duplicate"):
        prior = {"etag": "abc", "status": status}
        assert loader.decide_action(_obj("a.pdf", etag="abc"), prior, force=False) == "skip"


def test_decide_action_changed_etag_reingests() -> None:
    prior = {"etag": "old", "status": "ingested"}
    assert loader.decide_action(_obj("a.pdf", etag="new"), prior, force=False) == "ingest"


def test_decide_action_retries_a_failure_that_never_reached_the_corpus() -> None:
    # No document_id: the download itself failed, so nothing was recorded and
    # the next run must try again.
    prior = {"etag": "abc", "status": "failed", "document_id": None}
    assert loader.decide_action(_obj("a.pdf", etag="abc"), prior, force=False) == "ingest"


def test_decide_action_does_not_retry_an_extraction_failure() -> None:
    # Regression: a failed *extraction* left a status='failed' document row under
    # these exact bytes, so ingest_bytes short-circuits on the SHA before
    # extraction can re-run. Retrying only burned a full download and then
    # relabelled the object "duplicate" — terminal-OK — hiding the failure for
    # good. Unchanged bytes that already failed must be skipped.
    prior = {"etag": "abc", "status": "failed", "document_id": "doc-1"}
    assert loader.decide_action(_obj("a.pdf", etag="abc"), prior, force=False) == "skip"


def test_decide_action_reingests_a_failed_object_once_its_bytes_change() -> None:
    # The escape hatch for a permanent failure: fix the file (e.g. OCR it),
    # which changes its ETag, and it re-enters on the next run.
    prior = {"etag": "old", "status": "failed", "document_id": "doc-1"}
    assert loader.decide_action(_obj("a.pdf", etag="new"), prior, force=False) == "ingest"


def test_decide_action_force_overrides_skip() -> None:
    prior = {"etag": "abc", "status": "ingested"}
    assert loader.decide_action(_obj("a.pdf", etag="abc"), prior, force=True) == "ingest"


def test_supported_only_filters_by_extension() -> None:
    objs = [_obj("doc.pdf"), _obj("photo.jpg"), _obj("notes.md"), _obj("archive/")]
    assert [o.key for o in loader.supported_only(objs)] == ["doc.pdf", "notes.md"]


def _done(etag: str = "abc") -> dict[str, Any]:
    return {"etag": etag, "status": "ingested", "document_id": "doc-x"}


def test_plan_actions_limit_counts_ingests_not_objects_considered() -> None:
    # Regression: the limit used to slice the sorted key list *before* the
    # manifest was consulted, so an incremental --limit 2 against a bucket whose
    # leading keys were already loaded ingested nothing at all.
    objects = [_obj("a.pdf"), _obj("b.pdf"), _obj("c.pdf"), _obj("d.pdf")]
    state = {"a.pdf": _done(), "b.pdf": _done()}

    planned, held = loader.plan_actions(objects, state, force=False, limit=2)

    assert [(o.key, action) for o, action in planned] == [
        ("a.pdf", "skip"),
        ("b.pdf", "skip"),
        ("c.pdf", "ingest"),
        ("d.pdf", "ingest"),
    ]
    assert held == 0


def test_plan_actions_holds_back_ingests_past_the_limit() -> None:
    objects = [_obj("a.pdf"), _obj("b.pdf"), _obj("c.pdf")]

    planned, held = loader.plan_actions(objects, {}, force=False, limit=1)

    assert [(o.key, action) for o, action in planned] == [("a.pdf", "ingest")]
    assert held == 2


def test_plan_actions_without_a_limit_plans_everything() -> None:
    objects = [_obj("a.pdf"), _obj("b.pdf")]

    planned, held = loader.plan_actions(objects, {"a.pdf": _done()}, force=False, limit=None)

    assert [action for _o, action in planned] == ["skip", "ingest"]
    assert held == 0


def test_plan_actions_force_makes_every_object_an_ingest() -> None:
    objects = [_obj("a.pdf"), _obj("b.pdf")]

    planned, held = loader.plan_actions(objects, {"a.pdf": _done()}, force=True, limit=None)

    assert [action for _o, action in planned] == ["ingest", "ingest"]
    assert held == 0


def test_resolve_state_path_is_per_bucket_by_default() -> None:
    # Regression: a fixed default manifest shared across buckets let a key
    # present in two buckets mask the other's copy.
    args = loader.build_parser().parse_args(["--bucket", "other-bucket"])
    assert loader.resolve_state_path(args) == Path(loader.DEFAULT_STATE_DIR) / "other-bucket.json"


def test_resolve_state_path_honours_an_explicit_override() -> None:
    args = loader.build_parser().parse_args(["--state", "/tmp/x.json"])
    assert loader.resolve_state_path(args) == Path("/tmp/x.json")


def test_state_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "state.json"
    state = {"a.pdf": {"etag": "x", "size": 3, "status": "ingested", "document_id": "1"}}
    loader.save_state(path, state)
    assert loader.load_state(path) == state


def test_load_state_missing_returns_empty(tmp_path: Path) -> None:
    assert loader.load_state(tmp_path / "absent.json") == {}


def test_load_state_corrupt_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{ not json", encoding="utf-8")
    assert loader.load_state(path) == {}


def test_state_entry_records_etag_and_status() -> None:
    entry = loader._state_entry(_obj("a.pdf", etag="e1", size=42), "ingested", "doc-1")
    assert entry["etag"] == "e1"
    assert entry["size"] == 42
    assert entry["status"] == "ingested"
    assert entry["document_id"] == "doc-1"
    assert entry["updated_at"].endswith("Z")


def test_source_credentials_prefers_flags_then_env(monkeypatch) -> None:
    import argparse

    monkeypatch.setenv("SPACES_KEY", "envk")
    monkeypatch.setenv("SPACES_SECRET", "envs")
    flagged = argparse.Namespace(access_key="flagk", secret_key="flags")
    assert loader._source_credentials(flagged) == ("flagk", "flags")

    from_env = argparse.Namespace(access_key=None, secret_key=None)
    assert loader._source_credentials(from_env) == ("envk", "envs")


def test_source_credentials_anonymous_when_unset(monkeypatch) -> None:
    import argparse

    for var in ("SPACES_KEY", "SPACES_SECRET", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(var, raising=False)
    anon = argparse.Namespace(access_key=None, secret_key=None)
    assert loader._source_credentials(anon) == (None, None)


class _FakeResult:
    """Stand-in for pipeline.IngestResult (the loader only reads these fields)."""

    def __init__(self, status: str, document_id: str | None, detail: str = "") -> None:
        self.path = "src"
        self.status = status
        self.document_id = document_id
        self.detail = detail


def _ctx(**overrides: Any) -> loader.IngestContext:
    base: dict[str, Any] = {
        "conn": object(),
        "store": object(),
        "client": object(),
        "bucket": "src-bucket",
        "owner_principal_id": None,
        "chain": (),
    }
    base.update(overrides)
    return loader.IngestContext(**base)


def _stub_download(monkeypatch) -> None:
    monkeypatch.setattr(loader, "_download", lambda *_a, **_k: b"bytes")


def test_pipeline_chain_defaults_to_the_shared_post_ingest_chain() -> None:
    from doc_ray.domain.jobs import DEFAULT_PIPELINE_CHAIN

    args = argparse.Namespace(no_pipeline=False)
    assert loader.pipeline_chain(args) == tuple(DEFAULT_PIPELINE_CHAIN)


def test_pipeline_chain_empty_when_disabled() -> None:
    assert loader.pipeline_chain(argparse.Namespace(no_pipeline=True)) == ()


def test_ingest_one_delegates_finalization_to_the_shared_pipeline_step(monkeypatch) -> None:
    # Regression: the loader used to call enqueue_chain_head directly, which
    # chained the document but never refreshed its read-model rollup — the one
    # intake path that diverged from finalize_new_document (BL-162). Routing
    # through the shared step is what keeps the two obligations inseparable.
    _stub_download(monkeypatch)
    monkeypatch.setattr(
        loader, "ingest_object", lambda *_a, **_k: _FakeResult("ingested", "doc-1")
    )
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        loader,
        "finalize_new_document",
        lambda conn, result, chain: calls.append((result.document_id, tuple(chain))) or "job-1",
    )
    state: dict[str, dict[str, Any]] = {}

    status = loader._ingest_one(_ctx(chain=("annotate", "canonicalize")), _obj("a.pdf"), state)

    assert status == "ingested"
    assert calls == [("doc-1", ("annotate", "canonicalize"))]
    assert state["a.pdf"]["job_id"] == "job-1"


def test_ingest_one_does_not_chain_a_duplicate(monkeypatch) -> None:
    # A duplicate means the bytes are already in the corpus and already went
    # through the pipeline; re-chaining would re-annotate the whole corpus on
    # every --force run. The shared finalizer enforces this guard, so the loader
    # hands it every result and lets it decide.
    _stub_download(monkeypatch)
    monkeypatch.setattr(
        loader, "ingest_object", lambda *_a, **_k: _FakeResult("duplicate", "doc-1")
    )
    seen: list[str] = []
    monkeypatch.setattr(
        loader,
        "finalize_new_document",
        lambda _c, result, _chain: seen.append(result.status) or None,
    )
    state: dict[str, dict[str, Any]] = {}

    assert loader._ingest_one(_ctx(chain=("annotate",)), _obj("a.pdf"), state) == "duplicate"
    assert seen == ["duplicate"]
    assert state["a.pdf"]["job_id"] is None


def test_ingest_one_skips_chaining_when_pipeline_disabled(monkeypatch) -> None:
    _stub_download(monkeypatch)
    monkeypatch.setattr(
        loader, "ingest_object", lambda *_a, **_k: _FakeResult("ingested", "doc-1")
    )
    monkeypatch.setattr(
        loader,
        "finalize_new_document",
        lambda _c, _r, chain: pytest.fail("--no-pipeline must not enqueue") if chain else None,
    )
    state: dict[str, dict[str, Any]] = {}

    assert loader._ingest_one(_ctx(chain=()), _obj("a.pdf"), state) == "ingested"
    assert state["a.pdf"]["job_id"] is None


def test_ingest_one_threads_the_owner_and_source_reference_into_the_ingest_call(
    monkeypatch,
) -> None:
    _stub_download(monkeypatch)
    seen: dict[str, Any] = {}

    def _fake_ingest(conn, store, bucket, key, data, owner_principal_id=None):
        seen.update(bucket=bucket, key=key, owner=owner_principal_id)
        return _FakeResult("ingested", "doc-1")

    monkeypatch.setattr(loader, "ingest_object", _fake_ingest)
    monkeypatch.setattr(loader, "finalize_new_document", lambda *_a, **_k: "job-1")

    loader._ingest_one(_ctx(owner_principal_id="prin-9", chain=("annotate",)), _obj("a.pdf"), {})

    assert seen == {"bucket": "src-bucket", "key": "a.pdf", "owner": "prin-9"}


def test_ingest_object_records_the_source_reference_as_provenance(monkeypatch) -> None:
    # Regression: routing through a temp file discarded the object's origin, so
    # a corpus entry could not be traced back to the key it came from.
    seen: dict[str, Any] = {}

    def _fake_ingest_bytes(conn, store, data, **kwargs):
        seen.update(kwargs, data=data)
        return _FakeResult("ingested", "doc-1")

    monkeypatch.setattr(loader, "ingest_bytes", _fake_ingest_bytes)

    loader.ingest_object(object(), object(), "src-bucket", "papers/a.pdf", b"bytes", "prin-9")

    assert seen["source_url"] == "s3://src-bucket/papers/a.pdf"
    assert seen["filename"] == "a.pdf"  # basename drives extractor dispatch
    assert seen["owner_principal_id"] == "prin-9"
    assert seen["data"] == b"bytes"


def test_ingest_one_records_a_download_failure_without_a_document_id(monkeypatch) -> None:
    # The manifest entry is what tells the next run this failure is retryable:
    # no document id means the bytes never reached the corpus.
    from botocore.exceptions import BotoCoreError

    def _boom(*_a: Any, **_k: Any) -> bytes:
        raise BotoCoreError()

    monkeypatch.setattr(loader, "_download", _boom)
    monkeypatch.setattr(
        loader,
        "ingest_object",
        lambda *_a, **_k: pytest.fail("a failed download must not reach ingest"),
    )
    state: dict[str, dict[str, Any]] = {}

    assert loader._ingest_one(_ctx(chain=("annotate",)), _obj("a.pdf"), state) == "failed"
    assert state["a.pdf"]["document_id"] is None
    assert "download failed" in state["a.pdf"]["detail"]
    assert loader.decide_action(_obj("a.pdf"), state["a.pdf"], force=False) == "ingest"


def test_ingest_one_records_the_failure_reason_in_the_manifest(monkeypatch) -> None:
    # Regression: the manifest kept only a bare status, so diagnosing a bulk load
    # meant re-running it to read the console output again.
    _stub_download(monkeypatch)
    monkeypatch.setattr(
        loader,
        "ingest_object",
        lambda *_a, **_k: _FakeResult("failed", "doc-1", "no extractable text — OCR required"),
    )
    monkeypatch.setattr(loader, "finalize_new_document", lambda *_a, **_k: None)
    state: dict[str, dict[str, Any]] = {}

    assert loader._ingest_one(_ctx(chain=("annotate",)), _obj("a.pdf"), state) == "failed"
    assert "OCR required" in state["a.pdf"]["detail"]
    assert state["a.pdf"]["document_id"] == "doc-1"


def test_resolve_owner_returns_the_principal_id(monkeypatch) -> None:
    monkeypatch.setattr(loader, "find_principal_id_by_email", lambda _c, _e: "prin-7")
    assert loader.resolve_owner(object(), "someone@example.com") == "prin-7"


def test_resolve_owner_raises_for_an_unknown_email(monkeypatch) -> None:
    monkeypatch.setattr(loader, "find_principal_id_by_email", lambda _c, _e: None)
    with pytest.raises(loader.OwnerNotFound):
        loader.resolve_owner(object(), "ghost@example.com")


def test_parser_defaults_keep_the_pipeline_on_and_ownership_unset() -> None:
    args = loader.build_parser().parse_args([])
    assert args.no_pipeline is False
    assert args.owner is None
