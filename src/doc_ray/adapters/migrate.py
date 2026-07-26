"""Runtime migration runner (engineering decision D-006).

The Postgres container applies ``db/migrations`` only on first init (via
``/docker-entrypoint-initdb.d``), so migrations added after a volume exists never
reach it. This runner closes that gap: it applies every ``NNN_*.sql`` file not yet
recorded in ``schema_migrations``, in filename order, each in its own transaction,
under a session advisory lock so concurrent api/worker/one-shot boots don't race.

It is safe to run repeatedly and alongside the first-boot flow: every migration is
idempotent (``IF NOT EXISTS`` DDL) and self-records into ``schema_migrations``, so a
file already applied by initdb is skipped here, and vice versa.
"""

from __future__ import annotations

import contextlib
import os
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.rows import dict_row

if TYPE_CHECKING:
    from doc_ray.config import Config

# Stable 63-bit key for pg_advisory_lock so only one runner migrates at a time.
_ADVISORY_LOCK_KEY = 0x00D0C_2A17

# Migration files are `NNN_description.sql` (zero-padded ordinal prefix).
_MIGRATION_RE = re.compile(r"^\d{3}_.*\.sql$")

# Idempotent repairs that must converge old live databases even when their
# filename is already present in schema_migrations.
_REPLAYABLE_REPAIR_MIGRATIONS = frozenset({"054_document_layout_run_page_dimensions.sql"})
_LAYOUT_RUN_PAGE_DIMENSION_COLUMNS = frozenset({"page_width", "page_height"})
_LAYOUT_RUN_PAGE_DIMENSION_CONSTRAINTS = frozenset(
    {"document_layout_runs_page_width_check", "document_layout_runs_page_height_check"}
)


def _default_migrations_dir() -> Path:
    """Return the repository ``db/migrations`` directory.

    Overridable with ``DOCRAY_MIGRATIONS_DIR`` (used when the package is
    installed away from the repo tree, e.g. inside the container image).

    Returns:
        Path to the directory holding the ``NNN_*.sql`` migration files.
    """
    env = os.environ.get("DOCRAY_MIGRATIONS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "db" / "migrations"


def migration_files(migrations_dir: Path | None = None) -> list[Path]:
    """Return migration files sorted by their zero-padded ``NNN_`` prefix.

    Args:
        migrations_dir: Directory to scan; defaults to the repo's ``db/migrations``.

    Returns:
        The ``*.sql`` files whose names match ``NNN_*.sql``, in lexicographic
        (== numeric, because the prefix is zero-padded) order.
    """
    directory = migrations_dir or _default_migrations_dir()
    return sorted(p for p in directory.glob("*.sql") if _MIGRATION_RE.match(p.name))


def applied_filenames(conn: psycopg.Connection[Any]) -> set[str]:
    """Return migration filenames already recorded in ``schema_migrations``.

    A fresh database without the ``schema_migrations`` table yet is treated as
    "nothing applied", so the runner can bootstrap an empty database too.

    Args:
        conn: Live (autocommit) database connection. Any ``row_factory`` works;
            the ``filename`` column is read by name.

    Returns:
        The set of applied migration filenames.
    """
    table_row = conn.execute(
        "SELECT to_regclass('public.schema_migrations') AS table_name"
    ).fetchone()
    table_name = None if table_row is None else _optional_row_value(table_row, "table_name")
    if not table_name:
        return set()
    rows = conn.execute("SELECT filename AS filename FROM schema_migrations").fetchall()
    return {(row["filename"] if isinstance(row, dict) else row[0]) for row in rows}


def pending_migrations(
    conn: psycopg.Connection[Any], migrations_dir: Path | None = None
) -> list[Path]:
    """Return migrations present on disk but not yet in ``schema_migrations``.

    Args:
        conn: Live (autocommit) database connection.
        migrations_dir: Directory to scan; defaults to the repo's ``db/migrations``.

    Returns:
        Pending migration files in apply order.
    """
    applied = applied_filenames(conn)
    return [p for p in migration_files(migrations_dir) if p.name not in applied]


def _needs_autocommit(sql: str) -> bool:
    """Return whether *sql* must run outside an explicit transaction block.

    ``ALTER TYPE ... ADD VALUE`` is the one statement in this schema that is not
    reliably transaction-safe across Postgres versions, so a migration containing
    it is executed in autocommit mode instead of inside ``conn.transaction()``.
    """
    lowered = sql.lower()
    return "alter type" in lowered and "add value" in lowered


def _execute_migration(conn: psycopg.Connection[Any], path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    if _needs_autocommit(sql):
        conn.execute(sql)
    else:
        with conn.transaction():
            conn.execute(sql)


def _row_value(row: object, key: str, index: int = 0) -> str:
    if isinstance(row, Mapping):
        return str(row[key])
    if isinstance(row, Sequence) and not isinstance(row, (bytes, str)):
        return str(row[index])
    raise TypeError(f"unsupported row type: {type(row).__name__}")


def _optional_row_value(row: object, key: str, index: int = 0) -> object | None:
    if isinstance(row, Mapping):
        return row.get(key)
    if isinstance(row, Sequence) and not isinstance(row, (bytes, str)):
        return row[index] if len(row) > index else None
    raise TypeError(f"unsupported row type: {type(row).__name__}")


def _layout_run_page_dimensions_repair_needed(conn: psycopg.Connection[Any]) -> bool:
    columns = {
        _row_value(row, "column_name")
        for row in conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'document_layout_runs'
              AND column_name IN ('page_width', 'page_height')
            """
        ).fetchall()
    }
    constraints = {
        _row_value(row, "conname")
        for row in conn.execute(
            """
            SELECT conname
            FROM pg_constraint
            WHERE conname IN (
                'document_layout_runs_page_width_check',
                'document_layout_runs_page_height_check'
            )
            """
        ).fetchall()
    }
    return not (
        _LAYOUT_RUN_PAGE_DIMENSION_COLUMNS.issubset(columns)
        and _LAYOUT_RUN_PAGE_DIMENSION_CONSTRAINTS.issubset(constraints)
    )


def _repair_migration_needed(conn: psycopg.Connection[Any], migration_name: str) -> bool:
    if migration_name == "054_document_layout_run_page_dimensions.sql":
        return _layout_run_page_dimensions_repair_needed(conn)
    return False


def apply_migrations(
    cfg: Config,
    migrations_dir: Path | None = None,
    *,
    on_connection: Callable[[psycopg.Connection[Any] | None], None] | None = None,
) -> list[str]:
    """Apply every pending migration and return the filenames applied.

    Acquires a session advisory lock so a simultaneous api/worker/one-shot boot
    blocks rather than double-applying. Each transaction-safe migration runs inside
    its own ``BEGIN/COMMIT``; an ``ADD VALUE`` migration runs in autocommit. Both
    self-record into ``schema_migrations``, so a crash mid-run simply retries the
    unrecorded files next time.

    Args:
        cfg: Runtime configuration providing ``database_url``.
        migrations_dir: Directory to scan; defaults to the repo's ``db/migrations``.
        on_connection: Optional callback invoked with the live connection right
            after connecting, and again with ``None`` once it is no longer usable.
            The CLI entrypoint uses this to cancel an in-flight statement on
            SIGTERM/SIGINT so a routine ``docker compose down`` mid-migration exits
            cleanly instead of being SIGKILLed (exit 137, issue #4). Because every
            migration self-records and is idempotent, a canceled run just resumes
            on the next start.

    Returns:
        The list of migration filenames applied by this call (empty if none).
    """
    applied_now: list[str] = []
    with psycopg.connect(cfg.database_url, autocommit=True, row_factory=dict_row) as conn:
        if on_connection is not None:
            on_connection(conn)
        try:
            conn.execute("SELECT pg_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,))
            try:
                pending = pending_migrations(conn, migrations_dir)
                pending_names = {path.name for path in pending}
                for path in pending:
                    _execute_migration(conn, path)
                    applied_now.append(path.name)
                for path in migration_files(migrations_dir):
                    if (
                        path.name in _REPLAYABLE_REPAIR_MIGRATIONS
                        and path.name not in pending_names
                        and _repair_migration_needed(conn, path.name)
                    ):
                        _execute_migration(conn, path)
            finally:
                # The session advisory lock is also released when the connection
                # closes below, so suppress here: a connection already aborted by a
                # cancellation must not mask the QueryCanceled we want to surface.
                with contextlib.suppress(psycopg.Error):
                    conn.execute("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_KEY,))
        finally:
            if on_connection is not None:
                on_connection(None)
    return applied_now
