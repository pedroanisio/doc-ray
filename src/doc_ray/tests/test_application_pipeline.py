"""Ingest pipeline: hash → store blob → extract → persist (with a fake store)."""

from __future__ import annotations

from pathlib import Path

import _itdb
import pytest

from doc_ray.application import pipeline
from doc_ray.config import Config


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
