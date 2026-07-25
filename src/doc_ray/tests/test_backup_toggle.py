"""``DOCRAY_BACKUP_ENABLED`` — the master switch for the in-app backup subsystem.

The switch is only trustworthy if it holds at *every* enforcement point, so each
one gets a test in both positions: config parsing, the worker's dispatch gate,
the scheduler entrypoint, the GraphQL mutation guard, the ``backupEnabled``
capability field, and the webapp's routes and navigation.

The asymmetry that matters: a disabled deployment must not merely stop
*enqueueing* backups — it must also refuse to *execute* a backup row that is
already queued, because rows enqueued before the switch was thrown outlive the
config change (see ``test_run_once_cancels_a_queued_backup_when_disabled``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from doc_ray.adapters import queue_postgres as q
from doc_ray.application.jobs import run_once
from doc_ray.config import Config
from doc_ray.domain.jobs import BACKUP_JOB_KINDS, KIND_BACKUP, KIND_RESTORE, JobStatus
from doc_ray.entrypoints import backup_scheduler
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.mutation_guards import _require_backup_enabled

_DISABLED = tuple(sorted(BACKUP_JOB_KINDS))
_MIGRATIONS = Path(__file__).resolve().parents[3] / "db" / "migrations"


def _connect_or_skip():
    import _itdb

    try:
        conn = _itdb.connect()
    except Exception as exc:  # pragma: no cover - env dependent
        pytest.skip(f"DB unreachable: {exc}")
    conn.execute((_MIGRATIONS / "020_jobs.sql").read_text())  # idempotent
    return conn


@pytest.fixture
def qconn():
    conn = _connect_or_skip()
    created: list[str] = []
    try:
        yield conn, created
    finally:
        for job_id in created:
            conn.execute("DELETE FROM jobs WHERE id = %s", (job_id,))
        conn.close()


def _config(**overrides) -> Config:
    """A Config with only the required fields filled in, plus *overrides*."""
    return Config(
        database_url="postgresql://localhost/docray",
        s3_endpoint="http://localhost:9000",
        s3_access_key="key",
        s3_secret_key="secret",
        s3_bucket="documents",
        s3_region="us-east-1",
        **overrides,
    )


# --- the flag itself --------------------------------------------------------


def test_backup_is_disabled_by_default(monkeypatch):
    """An unset DOCRAY_BACKUP_ENABLED means off — the subsystem is opt-in."""
    monkeypatch.delenv("DOCRAY_BACKUP_ENABLED", raising=False)
    assert Config.from_env().backup_enabled is False


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on", " true "])
def test_backup_enabled_accepts_the_standard_truthy_spellings(monkeypatch, raw):
    monkeypatch.setenv("DOCRAY_BACKUP_ENABLED", raw)
    assert Config.from_env().backup_enabled is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "", "maybe"])
def test_backup_enabled_treats_anything_else_as_off(monkeypatch, raw):
    """Fail closed: an unparseable value disables rather than enables."""
    monkeypatch.setenv("DOCRAY_BACKUP_ENABLED", raw)
    assert Config.from_env().backup_enabled is False


def test_backup_job_kinds_covers_both_halves_of_the_subsystem():
    """Restore is gated with backup — a switch that leaves restore live is half-thrown."""
    assert {KIND_BACKUP, KIND_RESTORE} == BACKUP_JOB_KINDS


# --- worker dispatch --------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(BACKUP_JOB_KINDS))
def test_run_once_cancels_a_queued_backup_when_disabled(qconn, kind):
    """A row queued before the switch was thrown is canceled, not executed.

    This is the case that bit production: the scheduler had already enqueued a
    backup, so merely stopping the scheduler would have left the row queued and
    claimable forever.
    """
    conn, created = qconn
    ran: list[str] = []

    def handler(_c, _s, job):
        ran.append(job.id)
        return {}

    job_id = q.enqueue(conn, kind, {"note": "pre-switch"}, priority=0)
    created.append(job_id)

    done = run_once(
        conn,
        None,
        worker_id="w1",
        handlers={kind: handler},
        disabled_kinds=_DISABLED,
    )

    assert done is not None and done.id == job_id
    assert ran == [], "a disabled kind must never reach its handler"
    assert q.get(conn, job_id).status is JobStatus.CANCELED
    # The recorded reason is what an operator sees when asking why a backup
    # never ran, so assert on the message, not just the status.
    error = conn.execute("SELECT error FROM jobs WHERE id = %s", (job_id,)).fetchone()["error"]
    assert "disabled by configuration" in error


def test_run_once_runs_backup_normally_when_enabled(qconn):
    """The gate is the flag, not the kind: with the switch on, backup dispatches."""
    conn, created = qconn
    ran: list[str] = []

    def handler(_c, _s, job):
        ran.append(job.id)
        return {"ok": True}

    job_id = q.enqueue(conn, KIND_BACKUP, {}, priority=0)
    created.append(job_id)

    run_once(conn, None, worker_id="w1", handlers={KIND_BACKUP: handler}, disabled_kinds=None)

    assert ran == [job_id]
    assert q.get(conn, job_id).status is JobStatus.SUCCEEDED


def test_disabling_backup_does_not_touch_other_kinds(qconn):
    """The switch is scoped: ingest and friends still run on a backup-disabled worker."""
    conn, created = qconn
    ran: list[str] = []

    def handler(_c, _s, job):
        ran.append(job.id)
        return {}

    job_id = q.enqueue(conn, "unit-test", {}, priority=0)
    created.append(job_id)

    run_once(
        conn,
        None,
        worker_id="w1",
        handlers={"unit-test": handler},
        disabled_kinds=_DISABLED,
    )

    assert ran == [job_id]
    assert q.get(conn, job_id).status is JobStatus.SUCCEEDED


# --- scheduler entrypoint ---------------------------------------------------


def test_scheduler_exits_without_enqueueing_when_disabled(monkeypatch, capsys):
    """The scheduler exits 0 and never reaches the store or the queue."""
    monkeypatch.setenv("DOCRAY_BACKUP_ENABLED", "false")

    def _explode(*_a, **_k):  # pragma: no cover - asserts it is never called
        raise AssertionError("disabled scheduler must not build an object store")

    monkeypatch.setattr(backup_scheduler, "ObjectStore", _explode)
    monkeypatch.setattr(backup_scheduler, "run_once", _explode, raising=False)

    assert backup_scheduler.main(["--once"]) == 0
    assert "disabled" in capsys.readouterr().out


def test_scheduler_proceeds_when_enabled(monkeypatch):
    """With the switch on the scheduler builds its store and runs a pass."""
    monkeypatch.setenv("DOCRAY_BACKUP_ENABLED", "true")
    calls: list[str] = []

    class _Store:
        def ensure_bucket(self) -> None:
            calls.append("ensure_bucket")

    monkeypatch.setattr(backup_scheduler, "ObjectStore", lambda _cfg: _Store())
    monkeypatch.setattr(
        backup_scheduler.db,
        "connect",
        lambda _cfg: _NullConn(),
    )
    monkeypatch.setattr(
        backup_scheduler,
        "run_once",
        lambda *_a, **_k: (
            calls.append("run_once") or {"job_id": "j1", "active_job_id": None, "pruned": []}
        ),
    )

    assert backup_scheduler.main(["--once"]) == 0
    assert calls == ["ensure_bucket", "run_once"]


class _NullConn:
    """Context-manager stand-in for a database connection."""

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False


# --- GraphQL guard and capability field -------------------------------------


class _AuditingConn:
    """Minimal async connection double: records the audit writes the guard makes."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, sql, params=None):
        self.statements.append(str(sql))
        return self

    async def fetchone(self):
        return None


def _ctx(*, backup_enabled: bool | None) -> RequestContext:
    settings = None if backup_enabled is None else _config(backup_enabled=backup_enabled)
    conn = _AuditingConn()
    return RequestContext(conn=conn, principal=None, loaders=None, settings=settings)


def test_mutation_guard_rejects_when_disabled():
    ctx = _ctx(backup_enabled=False)
    with pytest.raises(PermissionError) as excinfo:
        asyncio.run(_require_backup_enabled(ctx, action="mutation.backup"))
    assert "DOCRAY_BACKUP_ENABLED" in str(excinfo.value)


def test_mutation_guard_rejects_when_settings_are_absent():
    """No settings on the context is not an implicit yes."""
    with pytest.raises(PermissionError):
        asyncio.run(_require_backup_enabled(_ctx(backup_enabled=None), action="mutation.backup"))


def test_mutation_guard_allows_when_enabled():
    asyncio.run(_require_backup_enabled(_ctx(backup_enabled=True), action="mutation.backup"))


@pytest.mark.parametrize("enabled", [True, False])
def test_backup_enabled_query_field_reports_the_flag(enabled):
    """Clients read the capability rather than probing with a mutation."""
    from doc_ray.entrypoints.api.schema import build_schema

    schema = build_schema()

    class _Info:
        context = _ctx(backup_enabled=enabled)

    result = asyncio.run(
        schema.execute("query { backupEnabled }", context_value=_Info.context),
    )
    assert result.errors is None, result.errors
    assert result.data == {"backupEnabled": enabled}
