"""Tests for the runtime migration runner (doc_ray.adapters.migrate)."""

from __future__ import annotations

import dataclasses
import signal
from collections import Counter

import psycopg
import pytest

from doc_ray.adapters import migrate
from doc_ray.config import Config
from doc_ray.entrypoints import migrate as migrate_entry


def test_migration_files_sorted_and_prefixed():
    files = migrate.migration_files()
    names = [p.name for p in files]
    assert names, "expected migration files on disk"
    assert names == sorted(names)
    assert names[0] == "001_init.sql"
    assert all(migrate._MIGRATION_RE.match(n) for n in names)
    prefixes = [name.split("_", 1)[0] for name in names]
    duplicates = {prefix: count for prefix, count in Counter(prefixes).items() if count > 1}
    assert duplicates == {}


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


def test_apply_migrations_registers_and_clears_the_active_connection(monkeypatch, tmp_path):
    """The runner must be handed the live connection (to cancel it) then told it is gone.

    This is the hook the CLI entrypoint uses to cancel an in-flight statement on
    SIGTERM/SIGINT (#4). ``on_connection`` is optional, so existing callers are
    unaffected.
    """
    (tmp_path / "001_init.sql").write_text(
        "INSERT INTO schema_migrations (filename) VALUES ('001_init.sql')"
    )
    events: list[str] = []

    class _FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def execute(self, sql, params=None):
            if "to_regclass" in sql:
                return _Rows([("schema_migrations",)])
            if sql == "SELECT filename AS filename FROM schema_migrations":
                return _Rows([])
            if sql.startswith("SELECT pg_advisory_"):
                return _Rows([])
            return _Rows([])

        def transaction(self):
            return self

    monkeypatch.setattr(migrate.psycopg, "connect", lambda *a, **kw: _FakeConnection())
    cfg = dataclasses.replace(Config.from_env(), database_url="postgresql://example/test")

    def recorder(conn):
        events.append("clear" if conn is None else "set")

    applied = migrate.apply_migrations(cfg, tmp_path, on_connection=recorder)

    assert applied == ["001_init.sql"]
    assert events == ["set", "clear"]


class _CancelSpyConnection:
    """Records ``cancel()``, the way the runner interrupts an in-flight statement."""

    def __init__(self) -> None:
        self.cancel_calls = 0

    def cancel(self) -> None:
        self.cancel_calls += 1


def _apply_that_fires_signal(signum: int, spy: _CancelSpyConnection):
    """A fake ``apply_migrations`` that invokes the runner's own installed handler
    mid-flight and then raises :class:`psycopg.errors.QueryCanceled` — exactly the
    error ``conn.cancel()`` produces when Postgres aborts the running statement."""

    def fake(cfg, migrations_dir=None, *, on_connection=None):
        assert on_connection is not None, "runner must register its connection for cancellation"
        on_connection(spy)
        handler = signal.getsignal(signum)
        assert callable(handler), "runner must install a handler for the signal"
        handler(signum, None)
        on_connection(None)
        raise psycopg.errors.QueryCanceled("canceling statement due to user request")

    return fake


def test_sigterm_during_migration_cancels_connection_and_exits_interrupted(monkeypatch, capsys):
    """SIGTERM cancels the in-flight statement and exits promptly and cleanly.

    Regression for #4: a routine ``docker compose down`` mid-migration must not
    escalate SIGTERM to SIGKILL (exit 137). The runner cancels the statement and
    exits with 128+SIGTERM; every migration is idempotent, so it resumes next start.
    """
    original = signal.getsignal(signal.SIGTERM)
    spy = _CancelSpyConnection()
    monkeypatch.setattr(
        migrate_entry.migrate_mod, "apply_migrations", _apply_that_fires_signal(signal.SIGTERM, spy)
    )

    code = migrate_entry.main([])

    assert code == 128 + signal.SIGTERM
    assert spy.cancel_calls == 1
    err = capsys.readouterr().err.lower()
    assert "interrupted" in err
    assert "resume" in err or "idempotent" in err
    assert "database unreachable" not in err
    assert signal.getsignal(signal.SIGTERM) is original, "signal handler must be restored"


def test_sigint_during_migration_cancels_connection_and_exits_130(monkeypatch, capsys):
    original = signal.getsignal(signal.SIGINT)
    spy = _CancelSpyConnection()
    monkeypatch.setattr(
        migrate_entry.migrate_mod, "apply_migrations", _apply_that_fires_signal(signal.SIGINT, spy)
    )

    code = migrate_entry.main([])

    assert code == 128 + signal.SIGINT
    assert spy.cancel_calls == 1
    assert signal.getsignal(signal.SIGINT) is original


def test_database_unreachable_without_signal_still_exits_2(monkeypatch, capsys):
    """A genuine connectivity failure (no signal) keeps the existing exit-2 contract."""

    def fake(cfg, migrations_dir=None, *, on_connection=None):
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(migrate_entry.migrate_mod, "apply_migrations", fake)

    code = migrate_entry.main([])

    assert code == 2
    err = capsys.readouterr().err.lower()
    assert "database unreachable" in err
    assert "interrupted" not in err


def test_successful_apply_returns_0_and_restores_handlers(monkeypatch, capsys):
    original_term = signal.getsignal(signal.SIGTERM)
    original_int = signal.getsignal(signal.SIGINT)

    def fake(cfg, migrations_dir=None, *, on_connection=None):
        assert on_connection is not None
        on_connection(object())
        on_connection(None)
        return ["999_demo.sql"]

    monkeypatch.setattr(migrate_entry.migrate_mod, "apply_migrations", fake)

    code = migrate_entry.main([])

    assert code == 0
    assert "applied 999_demo.sql" in capsys.readouterr().out
    assert signal.getsignal(signal.SIGTERM) is original_term
    assert signal.getsignal(signal.SIGINT) is original_int


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


def test_sigterm_cancels_a_real_in_flight_migration_promptly(tmp_path):
    """End-to-end reproduction of the incident (#4).

    Run the CLI runner as a real subprocess against a genuinely blocking migration
    (``SELECT pg_sleep(60)``), send SIGTERM once the statement is confirmed active,
    and assert the process cancels it and exits with 128+SIGTERM (143) in well
    under the sleep duration — proving it never waits for the 60s statement and is
    never SIGKILLed (exit 137). The sleep migration is unrecorded, so nothing to
    clean up.
    """
    import os
    import subprocess
    import sys
    import time

    import _itdb

    probe = _connect_or_skip()
    (tmp_path / "900_sleep_probe.sql").write_text("SELECT pg_sleep(60);")
    env = dict(os.environ)
    env["DATABASE_URL"] = _itdb.DB_URL
    env["DOCRAY_MIGRATIONS_DIR"] = str(tmp_path)

    proc = subprocess.Popen(
        [sys.executable, "-m", "doc_ray.entrypoints.migrate"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            # Exclude the probe's own backend: this very query text contains the
            # literal "pg_sleep(60)", so a bare LIKE would match itself and fire
            # SIGTERM before the subprocess has even installed its handler.
            active = probe.execute(
                "SELECT count(*) AS n FROM pg_stat_activity "
                "WHERE state = 'active' AND pid <> pg_backend_pid() "
                "AND query LIKE 'SELECT pg_sleep(60)%'"
            ).fetchone()
            if active["n"] if isinstance(active, dict) else active[0]:
                break
            if proc.poll() is not None:  # runner died before reaching the statement
                out, err = proc.communicate()
                pytest.fail(
                    f"runner exited early ({proc.returncode}). stderr:\n{err}\nstdout:\n{out}"
                )
            time.sleep(0.2)
        else:  # pragma: no cover - only on a hung/misconfigured backend
            proc.kill()
            pytest.fail("the pg_sleep migration never became active")

        proc.send_signal(signal.SIGTERM)
        start = time.monotonic()
        _out, err = proc.communicate(timeout=15)
        elapsed = time.monotonic() - start
        code = proc.returncode
    finally:
        if proc.poll() is None:  # pragma: no cover - defensive
            proc.kill()
        probe.close()

    assert code == 128 + signal.SIGTERM, f"expected clean 143, got {code}; stderr:\n{err}"
    assert elapsed < 10, (
        f"cancellation was not prompt ({elapsed:.1f}s) — it waited on the statement"
    )
    assert "interrupted by signal" in err
