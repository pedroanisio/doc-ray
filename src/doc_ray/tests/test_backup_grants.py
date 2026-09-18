"""Backup must be able to read every table it dumps — preflight, not mid-COPY death.

The defect this guards: every backup failed with

    InsufficientPrivilege: permission denied for table "web_auth_sessions"

A backup COPYs every public base table elevated to ``docray_batch``. Migration
106 granted that role access with a one-time ``GRANT ALL ON ALL TABLES`` and no
DEFAULT PRIVILEGES, so ``web_auth_sessions`` (migration 112) — and any future
table — was invisible to it, and the whole backup aborted mid-COPY with a raw
permission error and a half-written archive.

Migration 119 fixes the grant durably. This test covers the code-side hardening:
:func:`backup.unreadable_tables` detects the condition and
:func:`backup.create_backup`'s preflight raises a clear, table-naming error
*before* writing anything — so a forgotten grant on some later table fails loud
and early instead of silently or cryptically. It reproduces the exact condition
(a table the backup role cannot read) deterministically, regardless of the
ambient grants in whatever database runs the test.
"""

from __future__ import annotations

import pytest

_itdb = pytest.importorskip("_itdb")

from doc_ray.application import backup as bk  # noqa: E402


@pytest.fixture
def conn():
    c = _itdb.connect()
    try:
        yield c
    finally:
        c.rollback()
        c.close()


def _make_role(conn, name: str) -> None:
    conn.execute(
        "DO $$ BEGIN "
        f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{name}') "
        f"THEN CREATE ROLE {name} NOLOGIN; END IF; END $$"
    )


def test_unreadable_tables_flags_a_table_the_backup_role_cannot_select(conn):
    # A table only its creator can read reproduces the web_auth_sessions condition.
    _make_role(conn, "docray_backup_probe")
    conn.execute("DROP TABLE IF EXISTS _backup_probe_secret")
    conn.execute("CREATE TABLE _backup_probe_secret (id int)")
    conn.execute("REVOKE ALL ON _backup_probe_secret FROM PUBLIC")
    conn.execute("ALTER TABLE _backup_probe_secret OWNER TO CURRENT_USER")
    try:
        missing = bk.unreadable_tables(conn, ["_backup_probe_secret"], role="docray_backup_probe")
        assert "_backup_probe_secret" in missing

        conn.execute("GRANT SELECT ON _backup_probe_secret TO docray_backup_probe")
        assert (
            bk.unreadable_tables(conn, ["_backup_probe_secret"], role="docray_backup_probe") == []
        )
    finally:
        conn.execute("DROP TABLE IF EXISTS _backup_probe_secret")


def test_covered_tables_are_all_readable_by_the_backup_writer(conn):
    # The real invariant: after migration 119, docray_batch can read every
    # table a backup covers. If a future migration adds a table without the
    # default-privilege grant reaching it, this fails.
    tables = bk._covered_tables(conn)
    assert tables, "expected a populated schema"
    missing = bk.unreadable_tables(conn, tables, role="docray_batch")
    assert missing == [], f"docray_batch cannot read: {missing} — backup would fail on them"


def test_create_backup_preflights_and_names_the_unreadable_table(conn, monkeypatch):
    # The preflight must raise a clear, table-naming error before any archive
    # bytes are written — not die on a raw COPY partway through.
    monkeypatch.setattr(bk, "unreadable_tables", lambda *a, **k: ["web_auth_sessions"])

    class _NoStore:
        bucket = "test"

        def list_objects(self):  # pragma: no cover - must never be reached
            raise AssertionError("preflight should fail before touching the object store")

    doc = _itdb.make_doc(conn, "Preflight fixture.")  # noqa: F841
    with pytest.raises(RuntimeError, match="web_auth_sessions"):
        bk.create_backup(conn, _NoStore(), note="preflight")
