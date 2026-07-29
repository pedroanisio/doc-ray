#!/usr/bin/env python3
"""Measure whether the deferred H200 work list is empty using a read-only query."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import psycopg

PENDING_TAG = "pending-trf-h200"
PENDING_SQL = """
SELECT count(DISTINCT dt.document_id)
FROM document_tags dt
JOIN tags t ON t.id = dt.tag_id
WHERE t.tag = 'pending-trf-h200'
"""


@dataclass(frozen=True)
class ReadinessResult:
    """Stable machine-readable GPU-exit verdict."""

    status: str
    pending_documents: int | None
    detail: str


def load_env_file(path: Path) -> dict[str, str]:
    """Parse a simple dotenv file without logging any values."""
    env: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").lstrip().partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def check_readiness(
    env: Mapping[str, str], *, connect: Callable[..., Any] = psycopg.connect
) -> ReadinessResult:
    """Return ready only when no document carries the deferred-work tag."""
    database_url = (env.get("DATABASE_URL") or "").strip()
    if not database_url:
        return ReadinessResult("error", None, "DATABASE_URL is missing")
    try:
        with connect(database_url, autocommit=True) as conn:
            row = conn.execute(PENDING_SQL).fetchone()
            count = int(row[0]) if row else 0
    except Exception as exc:
        return ReadinessResult("error", None, f"{type(exc).__name__}: {exc}")
    if count:
        return ReadinessResult(
            "blocked",
            count,
            f"{count} document(s) still carry `{PENDING_TAG}`; GPU exit is blocked",
        )
    return ReadinessResult("ready", 0, f"no documents carry `{PENDING_TAG}`")


def main(argv: list[str] | None = None) -> int:
    """Run the readiness query and emit text or JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not args.env_file.is_file():
        result = ReadinessResult("error", None, f"env file not found: {args.env_file}")
    else:
        env = {**os.environ, **load_env_file(args.env_file)}
        result = check_readiness(env)
    if args.json:
        print(json.dumps(asdict(result), indent=2, sort_keys=True))
    else:
        print(f"gpu-exit: {result.status.upper()} — {result.detail}")
    return {"ready": 0, "blocked": 1, "error": 2}[result.status]


if __name__ == "__main__":
    raise SystemExit(main())
