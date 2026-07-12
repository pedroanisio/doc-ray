"""Backup/restore engine — full mechanism, isolated and fast.

Everything runs against a throwaway database and a throwaway object-store bucket
seeded with tiny data, so the tests never touch the working corpus or bucket and
stay quick (one migration apply). The roundtrip test proves restore is a true
point-in-time rewind: back up, mutate, restore, and the mutation is gone.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile

import _itdb
import psycopg
import pytest
from psycopg.rows import dict_row

from doc_ray.adapters import migrate as migrate_mod
from doc_ray.adapters.object_store import ObjectStore
from doc_ray.application import backup as bk
from doc_ray.config import Config
from doc_ray.domain import progress as domain_progress

_DBNAME = "docray_bkptest"


def _cfg(database_url: str, bucket: str) -> Config:
    base = Config.from_env()
    return Config(
        database_url=database_url,
        s3_endpoint=base.s3_endpoint,
        s3_access_key=base.s3_access_key,
        s3_secret_key=base.s3_secret_key,
        s3_bucket=bucket,
        s3_region=base.s3_region,
    )


@pytest.fixture(scope="module")
def isolated():
    """A migrated throwaway DB + a throwaway bucket. Yields (conn, store)."""
    try:
        admin = psycopg.connect(_itdb.DB_URL, autocommit=True)
    except psycopg.OperationalError as exc:
        pytest.skip(f"isolated backup env unavailable: {exc}")
    try:
        _itdb.require_safe_test_database(admin)
    except _itdb.UnsafeTestDatabaseError:
        admin.close()
        raise
    admin.execute(f"DROP DATABASE IF EXISTS {_DBNAME} WITH (FORCE)")
    admin.execute(f"CREATE DATABASE {_DBNAME} TEMPLATE template0")
    url = _itdb.DB_URL.rsplit("/", 1)[0] + f"/{_DBNAME}"
    cfg = _cfg(url, f"itbackup-{_itdb.unique_sha()[:10]}")
    try:
        migrate_mod.apply_migrations(cfg)
        store = ObjectStore(cfg)
        store.ensure_bucket()
    except Exception as exc:  # object store down, or migration failure
        admin.execute(f"DROP DATABASE IF EXISTS {_DBNAME} WITH (FORCE)")
        admin.close()
        pytest.skip(f"isolated backup env unavailable: {exc}")
    conn = psycopg.connect(url, row_factory=dict_row, autocommit=True)
    try:
        yield conn, store
    finally:
        conn.close()
        admin.execute(f"DROP DATABASE IF EXISTS {_DBNAME} WITH (FORCE)")
        admin.close()


def _seed_doc(conn, store, text: str) -> str:
    doc_id = _itdb.make_doc(conn, text)
    store.put(f"it/{doc_id}.txt", text.encode(), "text/plain")
    return doc_id


def test_covered_tables_excludes_operational(isolated):
    conn, _ = isolated
    tables = bk._covered_tables(conn)
    assert {"documents", "sentences", "canonical"} <= set(tables)
    assert "jobs" not in tables and "backups" not in tables  # operational/meta


def test_create_backup_archive_structure(isolated):
    conn, store = isolated
    doc_id = _seed_doc(conn, store, "Archive structure probe.")
    summary = bk.create_backup(conn, store, note="structure")

    assert summary["archive_bytes"] > 0
    assert summary["tables"]["documents"] >= 1 and summary["blob_count"] >= 1
    row = conn.execute(
        "SELECT status, object_key FROM backups WHERE id=%s", (summary["id"],)
    ).fetchone()
    assert row["status"] == "available" and row["object_key"] == summary["object_key"]

    with tarfile.open(fileobj=io.BytesIO(store.get(summary["object_key"])), mode="r:gz") as tar:
        names = set(tar.getnames())
        assert {"manifest.json", "db/documents.bin"} <= names
        assert any(n.startswith("blobs/") for n in names)
        manifest = json.loads(tar.extractfile("manifest.json").read())
    assert "jobs" not in [t["name"] for t in manifest["tables"]]
    assert conn.execute("SELECT 1 FROM documents WHERE id=%s", (doc_id,)).fetchone()


def test_create_backup_reports_table_blob_and_upload_progress(isolated):
    conn, store = isolated
    _seed_doc(conn, store, "Progress backup probe.")
    events: list[tuple[int | None, int | None, str | None]] = []
    token = domain_progress.set_progress_reporter(
        lambda current, total, stage: events.append((current, total, stage))
    )
    try:
        summary = bk.create_backup(conn, store, note="progress")
    finally:
        domain_progress.reset_progress_reporter(token)

    table_stages = [stage for _current, _total, stage in events if stage]
    assert summary["object_key"].startswith("backups/")
    assert "Preparing backup archive" in table_stages
    assert any(stage == "Backing up table documents" for stage in table_stages)
    assert any(stage == "Backing up object blobs" for stage in table_stages)
    assert table_stages[-1] == "Uploading backup archive"
    totals = {total for _current, total, _stage in events if total is not None}
    assert len(totals) == 1
    currents = [current for current, _total, _stage in events if current is not None]
    assert currents == sorted(currents)


def test_restore_is_point_in_time_rewind(isolated):
    conn, store = isolated
    keep = _seed_doc(conn, store, "This document is in the backup.")
    before_docs = conn.execute("SELECT count(*) AS n FROM documents").fetchone()["n"]
    keep_sents = conn.execute(
        "SELECT count(*) AS n FROM sentences WHERE document_id=%s", (keep,)
    ).fetchone()["n"]

    summary = bk.create_backup(conn, store, note="rewind")

    # Mutate after the snapshot: add a doc that the backup does NOT contain.
    after = _seed_doc(conn, store, "This document is NOT in the backup.")
    assert conn.execute("SELECT count(*) AS n FROM documents").fetchone()["n"] == before_docs + 1

    # Restore rewinds the whole corpus to the snapshot.
    archive = store.get(summary["object_key"])
    result = bk.restore_from_archive(conn, store, archive)
    assert result["schema_version"] == summary["schema_version"]

    assert conn.execute("SELECT count(*) AS n FROM documents").fetchone()["n"] == before_docs
    assert conn.execute("SELECT 1 FROM documents WHERE id=%s", (after,)).fetchone() is None  # gone
    assert (
        conn.execute("SELECT title FROM documents WHERE id=%s", (keep,)).fetchone()["title"]
        == "itest"
    )
    assert (
        conn.execute(
            "SELECT count(*) AS n FROM sentences WHERE document_id=%s", (keep,)
        ).fetchone()["n"]
        == keep_sents
    )
    # backups catalog itself survives a restore (it is an excluded table)
    assert conn.execute("SELECT 1 FROM backups WHERE id=%s", (summary["id"],)).fetchone()


def test_restore_rejects_schema_mismatch(isolated):
    conn, store = isolated
    # A hand-built archive whose manifest claims a different schema version.
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        payload = json.dumps(
            {"archive_version": 1, "schema_version": "0.0.0-nope", "tables": [], "sequences": {}}
        ).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    with pytest.raises(ValueError, match="schema mismatch"):
        bk.restore_from_archive(conn, store, buf.getvalue())


def test_create_backup_rejects_corrupt_document_blob(isolated):
    conn, store = isolated
    good = b"expected blob bytes"
    key = "it/corrupt-source.txt"
    doc_id = _itdb.make_doc(conn, good.decode(), source_uri=f"s3://{store.bucket}/{key}")
    conn.execute(
        "UPDATE documents SET content_sha256 = %s WHERE id = %s",
        (hashlib.sha256(good).hexdigest(), doc_id),
    )
    store.put(key, b"corrupt blob bytes", "text/plain")

    try:
        with pytest.raises(ValueError, match="sha256 mismatch"):
            bk.create_backup(conn, store, note="corrupt-source")

        row = conn.execute(
            "SELECT status, error FROM backups WHERE note = 'corrupt-source' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        assert row["status"] == "failed"
        assert "sha256 mismatch" in row["error"]
    finally:
        conn.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
        store.delete(key)


def test_restore_rejects_tampered_blob_member(isolated):
    conn, store = isolated
    current = bk._schema_version(conn)
    manifest = {
        "archive_version": 1,
        "schema_version": current,
        "tables": [],
        "sequences": {},
        "blobs": [
            {
                "key": "tampered/source.txt",
                "sha256": hashlib.sha256(b"original").hexdigest(),
                "bytes": len(b"original"),
            }
        ],
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        payload = json.dumps(manifest).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
        tampered = b"tampered"
        blob_info = tarfile.TarInfo("blobs/tampered/source.txt")
        blob_info.size = len(tampered)
        tar.addfile(blob_info, io.BytesIO(tampered))

    with pytest.raises(ValueError, match="sha256 mismatch"):
        bk.restore_from_archive(conn, store, buf.getvalue())


def test_restore_backup_rejects_unknown_id(isolated):
    conn, store = isolated
    with pytest.raises(ValueError, match="unknown backup"):
        bk.restore_backup(conn, store, "no-such-backup-id")


def test_restore_backup_rejects_a_row_with_no_archive(isolated):
    conn, store = isolated
    from doc_ray.domain.ulid import new_ulid_str

    backup_id = new_ulid_str()
    conn.execute("INSERT INTO backups (id, status) VALUES (%s, 'creating')", (backup_id,))
    try:
        with pytest.raises(ValueError, match="has no archive"):
            bk.restore_backup(conn, store, backup_id)
    finally:
        conn.execute("DELETE FROM backups WHERE id = %s", (backup_id,))


def test_restore_backup_delegates_through_the_catalog(isolated):
    # The public wrapper (looked up by id, fetched from the store) must produce
    # the same outcome as calling restore_from_archive directly.
    conn, store = isolated
    _seed_doc(conn, store, "Restore-by-id probe.")
    summary = bk.create_backup(conn, store, note="by-id")
    result = bk.restore_backup(conn, store, summary["id"])
    assert result["schema_version"] == summary["schema_version"]


def test_create_backup_marks_failed_and_reraises_on_error(isolated, monkeypatch):
    conn, store = isolated

    def boom(_conn, _store, _backup_id):
        raise RuntimeError("simulated dump failure")

    monkeypatch.setattr(bk, "_build_and_upload", boom)
    with pytest.raises(RuntimeError, match="simulated dump failure"):
        bk.create_backup(conn, store, note="will-fail")
    row = conn.execute(
        "SELECT status, error FROM backups WHERE note = 'will-fail' "
        "ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    assert row["status"] == "failed"
    assert "simulated dump failure" in row["error"]


def test_read_member_returns_empty_bytes_when_absent():
    # Private helper, unit-tested directly (no DB needed): a malformed/partial
    # archive's missing member must degrade to b"" rather than raising.
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz"):
        pass  # an empty archive — every lookup is a miss
    with tarfile.open(fileobj=io.BytesIO(buf.getvalue()), mode="r:gz") as tar:
        assert bk._read_member(tar, "nothing/here.bin") == b""


def test_restore_blobs_skips_unreadable_members():
    # A directory-only tarinfo entry under blobs/ has isfile()==False and is
    # skipped entirely; a real member is still restored — exercises both the
    # loop's filter and the ordinary count path in one pass.
    class _CountingStore:
        def __init__(self):
            self.puts = []

        def put(self, key, data, content_type):
            self.puts.append(key)

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        d = tarfile.TarInfo("blobs/subdir")
        d.type = tarfile.DIRTYPE
        tar.addfile(d)
        payload = b"blob bytes"
        f = tarfile.TarInfo("blobs/real.bin")
        f.size = len(payload)
        tar.addfile(f, io.BytesIO(payload))

    store = _CountingStore()
    with tarfile.open(fileobj=io.BytesIO(buf.getvalue()), mode="r:gz") as tar:
        n = bk._restore_blobs(tar, store, {})
    assert n == 1
    assert store.puts == ["real.bin"]


def test_schema_version_returns_none_on_db_error():
    class _BoomCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def execute(self, *_a, **_kw):
            raise psycopg.errors.UndefinedFunction("docray_schema_version() does not exist")

    class _BoomConn:
        def cursor(self, **_kw):
            return _BoomCursor()

    assert bk._schema_version(_BoomConn()) is None
