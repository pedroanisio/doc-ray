"""Unit tests for the pure logic of the Spaces ingest loader (scripts/ingest_from_spaces.py).

These cover the idempotency decision, extension filtering, and manifest round-trip
without touching the network, the database, or the object store.
"""

from __future__ import annotations

from pathlib import Path

import ingest_from_spaces as loader


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
