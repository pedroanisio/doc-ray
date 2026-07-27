"""Database probes for :mod:`prod_check`, split out to preserve D-015."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import psycopg
from _prod_check_queries import CORPUS_ROLLUP_HEALTH_SQL

from doc_ray.adapters import migrate as migrate_mod


@dataclass(frozen=True)
class CheckResult:
    """One probe's stable name, status, and operator-readable detail."""

    name: str
    status: str
    detail: str


def check_database(
    env: Mapping[str, str], *, connect: Callable[..., Any] = psycopg.connect
) -> CheckResult:
    """Confirm the managed cluster answers and carries the current schema."""
    try:
        with connect(env.get("DATABASE_URL", ""), autocommit=True) as conn:
            row = conn.execute("SELECT docray_schema_version()").fetchone()
            version = row[0] if row else "unknown"
            pending = list(migrate_mod.pending_migrations(conn))
    except Exception as exc:
        return CheckResult("database", "fail", f"{type(exc).__name__}: {exc}")
    if pending:
        names = ", ".join(str(p) for p in pending[:3])
        return CheckResult(
            "database",
            "fail",
            f"schema {version}, {len(pending)} pending migration(s): {names} "
            "— run `make prod-migrate`",
        )
    return CheckResult("database", "ok", f"schema {version}, no pending migrations")


def check_corpus_rollups(
    env: Mapping[str, str], *, connect: Callable[..., Any] = psycopg.connect
) -> CheckResult:
    """Fail when a tenant rollup disagrees with maintained rollup inputs.

    The trusted batch role sees tenant rollups without a request principal. This
    compares only maintained read models and never exposes tenant identifiers.
    """
    try:
        with connect(env.get("DATABASE_URL", ""), autocommit=True) as conn:
            conn.execute("SET ROLE docray_batch")
            row = conn.execute(CORPUS_ROLLUP_HEALTH_SQL).fetchone()
            failing = int(row[0]) if row else 0
    except Exception as exc:
        return CheckResult("corpus_rollups", "fail", f"{type(exc).__name__}: {exc}")
    if failing:
        return CheckResult(
            "corpus_rollups",
            "fail",
            f"{failing} tenant rollup(s) disagree with document/lexeme rollup inputs",
        )
    return CheckResult("corpus_rollups", "ok", "tenant rollups agree with maintained inputs")


__all__ = ["CheckResult", "check_corpus_rollups", "check_database"]
