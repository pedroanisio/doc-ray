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


def test_decide_action_prior_failure_retries() -> None:
    prior = {"etag": "abc", "status": "failed"}
    assert loader.decide_action(_obj("a.pdf", etag="abc"), prior, force=False) == "ingest"


def test_decide_action_force_overrides_skip() -> None:
    prior = {"etag": "abc", "status": "ingested"}
    assert loader.decide_action(_obj("a.pdf", etag="abc"), prior, force=True) == "ingest"


def test_supported_only_filters_by_extension() -> None:
    objs = [_obj("doc.pdf"), _obj("photo.jpg"), _obj("notes.md"), _obj("archive/")]
    assert [o.key for o in loader.supported_only(objs)] == ["doc.pdf", "notes.md"]


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


def test_ingest_one_enqueues_chain_head_after_a_real_ingest(monkeypatch) -> None:
    _stub_download(monkeypatch)
    monkeypatch.setattr(
        loader, "ingest_object", lambda *_a, **_k: _FakeResult("ingested", "doc-1")
    )
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        loader,
        "enqueue_chain_head",
        lambda conn, document_id, chain: calls.append((document_id, tuple(chain))) or "job-1",
    )
    state: dict[str, dict[str, Any]] = {}

    status = loader._ingest_one(_ctx(chain=("annotate", "canonicalize")), _obj("a.pdf"), state)

    assert status == "ingested"
    assert calls == [("doc-1", ("annotate", "canonicalize"))]
    assert state["a.pdf"]["job_id"] == "job-1"


def test_ingest_one_does_not_chain_a_duplicate(monkeypatch) -> None:
    # A duplicate means the bytes are already in the corpus and already went
    # through the pipeline; re-chaining would re-annotate the whole corpus on
    # every --force run. Mirrors _advance_chain's ingest guard.
    _stub_download(monkeypatch)
    monkeypatch.setattr(
        loader, "ingest_object", lambda *_a, **_k: _FakeResult("duplicate", "doc-1")
    )
    monkeypatch.setattr(
        loader,
        "enqueue_chain_head",
        lambda *_a, **_k: pytest.fail("duplicate must not enqueue pipeline work"),
    )

    assert loader._ingest_one(_ctx(chain=("annotate",)), _obj("a.pdf"), {}) == "duplicate"


def test_ingest_one_skips_chaining_when_pipeline_disabled(monkeypatch) -> None:
    _stub_download(monkeypatch)
    monkeypatch.setattr(
        loader, "ingest_object", lambda *_a, **_k: _FakeResult("ingested", "doc-1")
    )
    monkeypatch.setattr(
        loader,
        "enqueue_chain_head",
        lambda *_a, **_k: pytest.fail("--no-pipeline must not enqueue"),
    )
    state: dict[str, dict[str, Any]] = {}

    assert loader._ingest_one(_ctx(chain=()), _obj("a.pdf"), state) == "ingested"
    assert state["a.pdf"]["job_id"] is None


def test_ingest_one_threads_the_owner_into_the_ingest_call(monkeypatch) -> None:
    _stub_download(monkeypatch)
    seen: dict[str, Any] = {}

    def _fake_ingest(conn, store, key, data, owner_principal_id=None):
        seen["owner"] = owner_principal_id
        return _FakeResult("ingested", "doc-1")

    monkeypatch.setattr(loader, "ingest_object", _fake_ingest)
    monkeypatch.setattr(loader, "enqueue_chain_head", lambda *_a, **_k: "job-1")

    loader._ingest_one(_ctx(owner_principal_id="prin-9", chain=("annotate",)), _obj("a.pdf"), {})

    assert seen["owner"] == "prin-9"


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
