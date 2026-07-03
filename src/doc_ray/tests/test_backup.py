"""Backup/restore engine — full mechanism, isolated and fast.

Everything runs against a throwaway database and a throwaway object-store bucket
seeded with tiny data, so the tests never touch the working corpus or bucket and
stay quick (one migration apply). The roundtrip test proves restore is a true
point-in-time rewind: back up, mutate, restore, and the mutation is gone.
"""

from __future__ import annotations

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
    admin = psycopg.connect(_itdb.DB_URL, autocommit=True)
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
