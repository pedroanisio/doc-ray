"""API backup-download route: admin-only, serves an ``available`` archive, 404s otherwise.

Runs against the integration database (`_itdb`) and the configured object store.
It inserts one throwaway ``backups`` row plus one object and removes both in a
``finally``, so the live corpus tables are never mutated. The webapp proxies this
same route, so covering it here covers the browser download path's server side.

BL-094: the route used to run unauthenticated on the raw superuser pool
connection, bypassing both the HTTP layer and the backups RLS policy
(migration 043) entirely. It now requires an authenticated admin principal,
the same as createBackup/restoreBackup (BL-064/D-019).
"""

from __future__ import annotations

import contextlib
from uuid import uuid4

import _itdb
import psycopg
import pytest
from psycopg.rows import dict_row

from doc_ray.adapters.object_store import ObjectStore
from doc_ray.config import Config
from doc_ray.domain.ulid import new_ulid_str

ADMIN_TOKEN = "test-admin-token"
NON_ADMIN_TOKEN = "test-non-admin-token"


def _cfg(**overrides) -> Config:
    base = Config.from_env()
    return Config(
        database_url=_itdb.DB_URL,
        s3_endpoint=base.s3_endpoint,
        s3_access_key=base.s3_access_key,
        s3_secret_key=base.s3_secret_key,
        s3_bucket=base.s3_bucket,
        s3_region=base.s3_region,
        **overrides,
    )


def test_download_serves_available_archive_and_404s_for_admin() -> None:
    from fastapi.testclient import TestClient

    from doc_ray.entrypoints.api.app import create_app

    admin_id = str(uuid4())
    conn = psycopg.connect(_itdb.DB_URL, row_factory=dict_row, autocommit=True)
    _itdb.ensure_admin_principal(conn, admin_id, "test-backup-download-admin")
    cfg = _cfg(api_tokens={ADMIN_TOKEN: (admin_id, frozenset({"read", "write"}))})
    try:
        store = ObjectStore(cfg)
        store.ensure_bucket()
    except Exception as exc:  # object store unreachable
        pytest.skip(f"object store unavailable: {exc}")

    backup_id = new_ulid_str()
    key = f"backups/test-{backup_id}.tar.gz"
    payload = b"\x1f\x8b" + backup_id.encode()  # gzip magic + marker (opaque bytes)
    headers = {"authorization": f"Bearer {ADMIN_TOKEN}"}
    try:
        store.put(key, payload, "application/gzip")
        conn.execute(
            "INSERT INTO backups (id, status, object_key, schema_version) "
            "VALUES (%s, 'available', %s, '1.3.0')",
            (backup_id, key),
        )
        with TestClient(create_app(cfg)) as client:
            ok = client.get(f"/backups/{backup_id}/download", headers=headers)
            assert ok.status_code == 200
            assert ok.content == payload
            assert ok.headers["content-type"] == "application/gzip"
            assert backup_id in ok.headers.get("content-disposition", "")

            # Unknown id -> 404 (for an admin principal).
            assert (
                client.get(f"/backups/{new_ulid_str()}/download", headers=headers).status_code
                == 404
            )

            # A non-available backup is not downloadable -> 404.
            conn.execute("UPDATE backups SET status='creating' WHERE id=%s", (backup_id,))
            assert client.get(f"/backups/{backup_id}/download", headers=headers).status_code == 404
    finally:
        conn.execute("DELETE FROM backups WHERE id=%s", (backup_id,))
        conn.execute("DELETE FROM principal_roles WHERE principal_id = %s", (admin_id,))
        conn.execute("DELETE FROM principals WHERE id = %s", (admin_id,))
        conn.close()
        with contextlib.suppress(Exception):  # best-effort blob cleanup
            store._client.delete_object(Bucket=cfg.s3_bucket, Key=key)


def test_download_rejects_anonymous_caller() -> None:
    """No credentials at all -> 401, never the archive bytes (BL-094)."""
    from fastapi.testclient import TestClient

    from doc_ray.entrypoints.api.app import create_app

    cfg = _cfg()
    with TestClient(create_app(cfg)) as client:
        r = client.get(f"/backups/{new_ulid_str()}/download")
        assert r.status_code == 401


def test_download_rejects_non_admin_write_scoped_caller() -> None:
    """Global write scope alone must not authorize a corpus-wide download (BL-094)."""
    from fastapi.testclient import TestClient

    from doc_ray.entrypoints.api.app import create_app

    conn = psycopg.connect(_itdb.DB_URL, row_factory=dict_row, autocommit=True)
    principal_id = conn.execute(
        "INSERT INTO principals (kind, display_name) VALUES ('human', %s) RETURNING id",
        (f"test-non-admin-{new_ulid_str()}",),
    ).fetchone()["id"]
    cfg = _cfg(api_tokens={NON_ADMIN_TOKEN: (str(principal_id), frozenset({"read", "write"}))})
    try:
        with TestClient(create_app(cfg)) as client:
            r = client.get(
                f"/backups/{new_ulid_str()}/download",
                headers={"authorization": f"Bearer {NON_ADMIN_TOKEN}"},
            )
            assert r.status_code == 403
    finally:
        conn.execute("DELETE FROM principals WHERE id = %s", (principal_id,))
        conn.close()
