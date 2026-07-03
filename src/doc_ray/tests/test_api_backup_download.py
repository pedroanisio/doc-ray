"""API backup-download route: serves an ``available`` archive, 404s otherwise.

Runs against the integration database (`_itdb`) and the configured object store.
It inserts one throwaway ``backups`` row plus one object and removes both in a
``finally``, so the live corpus tables are never mutated. The webapp proxies this
same route, so covering it here covers the browser download path's server side.
"""

from __future__ import annotations

import contextlib

import _itdb
import psycopg
import pytest
from psycopg.rows import dict_row

from doc_ray.adapters.object_store import ObjectStore
from doc_ray.config import Config
from doc_ray.domain.ulid import new_ulid_str


def _cfg() -> Config:
    base = Config.from_env()
    return Config(
        database_url=_itdb.DB_URL,
        s3_endpoint=base.s3_endpoint,
        s3_access_key=base.s3_access_key,
        s3_secret_key=base.s3_secret_key,
        s3_bucket=base.s3_bucket,
        s3_region=base.s3_region,
    )


def test_download_serves_available_archive_and_404s() -> None:
    from fastapi.testclient import TestClient

    from doc_ray.entrypoints.api.app import create_app

    cfg = _cfg()
    try:
        store = ObjectStore(cfg)
        store.ensure_bucket()
    except Exception as exc:  # object store unreachable
        pytest.skip(f"object store unavailable: {exc}")

    backup_id = new_ulid_str()
    key = f"backups/test-{backup_id}.tar.gz"
    payload = b"\x1f\x8b" + backup_id.encode()  # gzip magic + marker (opaque bytes)
    conn = psycopg.connect(cfg.database_url, row_factory=dict_row, autocommit=True)
    try:
        store.put(key, payload, "application/gzip")
        conn.execute(
            "INSERT INTO backups (id, status, object_key, schema_version) "
            "VALUES (%s, 'available', %s, '1.3.0')",
            (backup_id, key),
        )
        with TestClient(create_app(cfg)) as client:
            ok = client.get(f"/backups/{backup_id}/download")
            assert ok.status_code == 200
            assert ok.content == payload
            assert ok.headers["content-type"] == "application/gzip"
            assert backup_id in ok.headers.get("content-disposition", "")

            # Unknown id -> 404.
            assert client.get(f"/backups/{new_ulid_str()}/download").status_code == 404

            # A non-available backup is not downloadable -> 404.
            conn.execute("UPDATE backups SET status='creating' WHERE id=%s", (backup_id,))
            assert client.get(f"/backups/{backup_id}/download").status_code == 404
    finally:
        conn.execute("DELETE FROM backups WHERE id=%s", (backup_id,))
        conn.close()
        with contextlib.suppress(Exception):  # best-effort blob cleanup
            store._client.delete_object(Bucket=cfg.s3_bucket, Key=key)
