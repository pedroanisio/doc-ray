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


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _TupleRowConnection:
    def __init__(self):
        self.executed: list[str] = []

    def execute(self, sql):
        self.executed.append(sql)
        if "to_regclass" in sql:
            return _Rows([("schema_migrations",)])
        assert sql == "SELECT filename AS filename FROM schema_migrations"
        return _Rows([("001_init.sql",), ("002_normalize_4nf.sql",)])


def test_applied_filenames_accepts_tuple_rows():
    conn = _TupleRowConnection()
    applied = migrate.applied_filenames(conn)

    assert applied == {"001_init.sql", "002_normalize_4nf.sql"}
    assert conn.executed == [
        "SELECT to_regclass('public.schema_migrations') AS table_name",
        "SELECT filename AS filename FROM schema_migrations",
    ]


def test_applied_filenames_avoids_missing_schema_migrations_select():
    class _MissingSchemaMigrationsConnection:
        def __init__(self):
            self.executed: list[str] = []

        def execute(self, sql):
            self.executed.append(sql)
            if "to_regclass" in sql:
                return _Rows([(None,)])
            raise AssertionError("should not query absent schema_migrations")

    conn = _MissingSchemaMigrationsConnection()

    assert migrate.applied_filenames(conn) == set()
    assert conn.executed == ["SELECT to_regclass('public.schema_migrations') AS table_name"]


def test_apply_migrations_replays_recorded_idempotent_repairs(monkeypatch, tmp_path):
    repair_name = "054_document_layout_run_page_dimensions.sql"
    (tmp_path / "001_init.sql").write_text(
        "INSERT INTO schema_migrations (filename) VALUES ('001_init.sql')"
    )
    (tmp_path / repair_name).write_text(
        "ALTER TABLE document_layout_runs ADD COLUMN IF NOT EXISTS page_width double precision"
    )
    executed_sql: list[str] = []

    class _FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def execute(self, sql, params=None):
            if "to_regclass" in sql:
                return _Rows([("schema_migrations",)])
            if sql == "SELECT filename AS filename FROM schema_migrations":
                return _Rows([("001_init.sql",), (repair_name,)])
            if sql.startswith("SELECT pg_advisory_"):
                return _Rows([])
            if "information_schema.columns" in sql:
                return _Rows([("page_width",)])
            if "FROM pg_constraint" in sql:
                return _Rows([])
            executed_sql.append(sql)
            return _Rows([])

        def transaction(self):
            return self

    monkeypatch.setattr(migrate.psycopg, "connect", lambda *a, **kw: _FakeConnection())
    cfg = dataclasses.replace(Config.from_env(), database_url="postgresql://example/test")

    assert migrate.apply_migrations(cfg, tmp_path) == []
    assert executed_sql == [
        "ALTER TABLE document_layout_runs ADD COLUMN IF NOT EXISTS page_width double precision"
    ]


def test_apply_migrations_skips_recorded_repair_when_catalog_is_converged(monkeypatch, tmp_path):
    repair_name = "054_document_layout_run_page_dimensions.sql"
    (tmp_path / repair_name).write_text("SELECT fail_if_replayed()")
    executed_sql: list[str] = []

    class _FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def execute(self, sql, params=None):
            if "to_regclass" in sql:
                return _Rows([("schema_migrations",)])
            if sql == "SELECT filename AS filename FROM schema_migrations":
                return _Rows([(repair_name,)])
            if sql.startswith("SELECT pg_advisory_"):
                return _Rows([])
            if "information_schema.columns" in sql:
                return _Rows([("page_width",), ("page_height",)])
            if "FROM pg_constraint" in sql:
                return _Rows(
                    [
                        ("document_layout_runs_page_width_check",),
                        ("document_layout_runs_page_height_check",),
                    ]
                )
            executed_sql.append(sql)
            return _Rows([])

        def transaction(self):
            return self

    monkeypatch.setattr(migrate.psycopg, "connect", lambda *a, **kw: _FakeConnection())
    cfg = dataclasses.replace(Config.from_env(), database_url="postgresql://example/test")

    assert migrate.apply_migrations(cfg, tmp_path) == []
    assert executed_sql == []


def test_apply_migrations_repairs_recorded_tenant_foundation_grant(monkeypatch, tmp_path):
    """A DB that already recorded 061 still receives the forward tenant grant repair."""
    tenant_foundation = "061_tenant_foundation.sql"
    tenant_grant_repair = "085_tenant_catalog_client_select.sql"
    version_bump = "086_schema_version_1_27_0.sql"
    (tmp_path / tenant_foundation).write_text(
        "INSERT INTO schema_migrations (filename) VALUES ('061_tenant_foundation.sql')"
    )
    (tmp_path / tenant_grant_repair).write_text(
        "GRANT SELECT ON tenants TO docray_client;\n"
        "INSERT INTO schema_migrations (filename) "
        "VALUES ('085_tenant_catalog_client_select.sql')"
    )
    (tmp_path / version_bump).write_text(
        "INSERT INTO schema_version (singleton, version) VALUES (true, '1.27.0');\n"
        "INSERT INTO schema_migrations (filename) VALUES ('086_schema_version_1_27_0.sql')"
    )
    executed_sql: list[str] = []

    class _FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def execute(self, sql, params=None):
            if "to_regclass" in sql:
                return _Rows([("schema_migrations",)])
            if sql == "SELECT filename AS filename FROM schema_migrations":
                return _Rows([(tenant_foundation,)])
            if sql.startswith("SELECT pg_advisory_"):
                return _Rows([])
            executed_sql.append(sql)
            return _Rows([])

        def transaction(self):
            return self

    monkeypatch.setattr(migrate.psycopg, "connect", lambda *a, **kw: _FakeConnection())
    cfg = dataclasses.replace(Config.from_env(), database_url="postgresql://example/test")

    assert migrate.apply_migrations(cfg, tmp_path) == [tenant_grant_repair, version_bump]
    assert "GRANT SELECT ON tenants TO docray_client" in executed_sql[0]


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
        columns = {
            r["column_name"]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'document_layout_runs'"
            ).fetchall()
        }
        assert {"page_width", "page_height"}.issubset(columns)
    finally:
        conn.close()
