"""Tests for the runtime migration runner (doc_ray.adapters.migrate)."""

from __future__ import annotations

import dataclasses

import pytest

from doc_ray.adapters import migrate
from doc_ray.config import Config


def test_migration_files_sorted_and_prefixed():
    files = migrate.migration_files()
    names = [p.name for p in files]
    assert names, "expected migration files on disk"
    assert names == sorted(names)
    assert names[0] == "001_init.sql"
    assert all(migrate._MIGRATION_RE.match(n) for n in names)


def test_default_migrations_dir_points_at_db_migrations():
    directory = migrate._default_migrations_dir()
    assert directory.name == "migrations"
    assert directory.parent.name == "db"


def test_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCRAY_MIGRATIONS_DIR", str(tmp_path))
    assert migrate._default_migrations_dir() == tmp_path


def test_migration_files_from_custom_dir(tmp_path):
    (tmp_path / "003_c.sql").write_text("-- c")
    (tmp_path / "001_a.sql").write_text("-- a")
    (tmp_path / "002_b.sql").write_text("-- b")
    (tmp_path / "notes.txt").write_text("ignored")
    (tmp_path / "zz_not_numbered.sql").write_text("ignored")
    names = [p.name for p in migrate.migration_files(tmp_path)]
    assert names == ["001_a.sql", "002_b.sql", "003_c.sql"]


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("ALTER TYPE provenance_kind ADD VALUE IF NOT EXISTS 'annotated'", True),
        ("alter type t add value 'y'", True),
        ("CREATE TABLE t (id int)", False),
        ("INSERT INTO t VALUES (1)", False),
    ],
)
def test_needs_autocommit(sql, expected):
    assert migrate._needs_autocommit(sql) is expected


def _connect_or_skip():
    import _itdb

    try:
        return _itdb.connect()
    except Exception as exc:  # pragma: no cover - env dependent
        pytest.skip(f"DB unreachable: {exc}")


def test_apply_is_idempotent_against_live_db():
    import _itdb

    conn = _connect_or_skip()
    try:
        cfg = dataclasses.replace(Config.from_env(), database_url=_itdb.DB_URL)
        migrate.apply_migrations(cfg)  # ensure fully applied first
        assert migrate.apply_migrations(cfg) == []  # a second run is a no-op
        assert migrate.pending_migrations(conn) == []
        applied = migrate.applied_filenames(conn)
        assert "001_init.sql" in applied
    finally:
        conn.close()
