"""Command-line entrypoint for the runtime migration runner (D-006).

Usage::

    python -m doc_ray.entrypoints.migrate            # apply pending migrations
    python -m doc_ray.entrypoints.migrate --dry-run  # list pending, apply nothing

Exit codes: 0 = success (or nothing pending); 2 = the database is unreachable.
"""

from __future__ import annotations

import argparse
import sys

import psycopg

from doc_ray.adapters import migrate as migrate_mod
from doc_ray.config import Config


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="doc_ray.entrypoints.migrate",
        description="Apply pending database migrations recorded in schema_migrations.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List migrations that would be applied, then exit without applying.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Apply pending migrations (or list them with ``--dry-run``).

    Args:
        argv: Optional argument vector (defaults to ``sys.argv``).

    Returns:
        Process exit code: 0 on success, 2 if the database is unreachable.
    """
    args = _build_parser().parse_args(argv)
    cfg = Config.from_env()

    try:
        if args.dry_run:
            with psycopg.connect(cfg.database_url, autocommit=True) as conn:
                pending = migrate_mod.pending_migrations(conn)
            for path in pending:
                print(f"pending {path.name}")
            print(f"{len(pending)} pending")
            return 0

        applied = migrate_mod.apply_migrations(cfg)
    except psycopg.OperationalError as exc:
        print(f"error: database unreachable: {exc}", file=sys.stderr)
        return 2

    for name in applied:
        print(f"applied {name}")
    print(f"{len(applied)} applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
