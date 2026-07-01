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

import os
import re
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
    try:
        rows = conn.execute("SELECT filename AS filename FROM schema_migrations").fetchall()
    except psycopg.errors.UndefinedTable:
        return set()
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


def apply_migrations(cfg: Config, migrations_dir: Path | None = None) -> list[str]:
    """Apply every pending migration and return the filenames applied.

    Acquires a session advisory lock so a simultaneous api/worker/one-shot boot
    blocks rather than double-applying. Each transaction-safe migration runs inside
    its own ``BEGIN/COMMIT``; an ``ADD VALUE`` migration runs in autocommit. Both
    self-record into ``schema_migrations``, so a crash mid-run simply retries the
    unrecorded files next time.

    Args:
        cfg: Runtime configuration providing ``database_url``.
        migrations_dir: Directory to scan; defaults to the repo's ``db/migrations``.

    Returns:
        The list of migration filenames applied by this call (empty if none).
    """
    applied_now: list[str] = []
    with psycopg.connect(cfg.database_url, autocommit=True, row_factory=dict_row) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,))
        try:
            for path in pending_migrations(conn, migrations_dir):
                sql = path.read_text(encoding="utf-8")
                if _needs_autocommit(sql):
                    conn.execute(sql)
                else:
                    with conn.transaction():
                        conn.execute(sql)
                applied_now.append(path.name)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_KEY,))
    return applied_now
