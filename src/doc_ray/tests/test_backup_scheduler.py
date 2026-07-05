"""Scheduled backup enqueueing and retention pruning."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from doc_ray.application import backup as bk
from doc_ray.domain.jobs import KIND_BACKUP
from doc_ray.domain.ulid import new_ulid_str
from doc_ray.entrypoints import backup_scheduler


class _DeletingStore:
    bucket = "test"

    def __init__(self) -> None:
        self.deleted: list[str] = []

    def ensure_bucket(self) -> None:
        pass

    def put(self, key: str, data: bytes, content_type: str) -> str:
        return f"s3://test/{key}"

    def get(self, key: str) -> bytes:
        return b""

    def list_objects(self, prefix: str = "") -> list[str]:
        return []

    def delete(self, key: str) -> None:
        self.deleted.append(key)


def test_prune_backups_deletes_old_archives_and_catalog_rows(pg):
    conn, _created = pg
    store = _DeletingStore()
    now = datetime(2026, 7, 5, tzinfo=timezone.utc)
    old_id = new_ulid_str()
    keep_id = new_ulid_str()
    try:
        conn.execute(
            "INSERT INTO backups (id, status, object_key, created_at) "
            "VALUES (%s, 'available', %s, %s), (%s, 'available', %s, %s)",
            (
                old_id,
                "backups/old.tar.gz",
                now - timedelta(days=40),
                keep_id,
                "backups/keep.tar.gz",
                now,
            ),
        )

        pruned = bk.prune_backups(
            conn,
            store,
            keep_latest=1,
            max_age_days=30,
            now=now,
        )

        assert pruned == [{"id": old_id, "object_key": "backups/old.tar.gz"}]
        assert store.deleted == ["backups/old.tar.gz"]
        assert conn.execute("SELECT 1 FROM backups WHERE id = %s", (old_id,)).fetchone() is None
        assert conn.execute("SELECT 1 FROM backups WHERE id = %s", (keep_id,)).fetchone()
    finally:
        conn.execute("DELETE FROM backups WHERE id IN (%s, %s)", (old_id, keep_id))


def test_scheduler_run_once_enqueues_backup_and_prunes_retention(pg):
    conn, _created = pg
    store = _DeletingStore()
    old_id = new_ulid_str()
    try:
        conn.execute(
            "DELETE FROM jobs WHERE kind = %s AND status IN ('queued', 'running')", (KIND_BACKUP,)
        )
        conn.execute(
            "INSERT INTO backups (id, status, object_key, created_at) "
            "VALUES (%s, 'available', %s, now() - interval '40 days')",
            (old_id, "backups/expired.tar.gz"),
        )

        result = backup_scheduler.run_once(
            conn,
            store,
            keep_latest=0,
            max_age_days=30,
            note="scheduled test",
        )

        assert result["job_id"]
        assert result["pruned"] == [{"id": old_id, "object_key": "backups/expired.tar.gz"}]
        row = conn.execute(
            "SELECT kind, payload FROM jobs WHERE id = %s", (result["job_id"],)
        ).fetchone()
        assert row["kind"] == KIND_BACKUP
        assert row["payload"]["note"] == "scheduled test"
    finally:
        conn.execute(
            "DELETE FROM jobs WHERE kind = %s AND payload->>'note' = 'scheduled test'",
            (KIND_BACKUP,),
        )
        conn.execute("DELETE FROM backups WHERE id = %s", (old_id,))
