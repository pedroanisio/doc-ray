"""Orchestration and CLI for a db_analyzer run.

Connects through the configured engine (read-only by default — see
:meth:`~doc_ray.application.db_analyzer.engines.base.Engine.connect`),
inspects structure, profiles data, derives issues, and writes both report
formats to disk.

    python -m doc_ray.entrypoints.db_analyze                    # Markdown to stdout + files
    python -m doc_ray.entrypoints.db_analyze --json              # JSON to stdout + files
    python -m doc_ray.entrypoints.db_analyze --structure-only    # skip data profiling
    python -m doc_ray.entrypoints.db_analyze --schemas public

Exit codes: ``0`` no warnings/errors found, ``2`` when any are found, ``1``
when the report cannot be produced at all (bad config or unreachable database).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg

from doc_ray.application.db_analyzer.config import AnalyzerConfig, load_config
from doc_ray.application.db_analyzer.engines.base import Engine
from doc_ray.application.db_analyzer.engines.registry import get_engine
from doc_ray.application.db_analyzer.models import AnalysisResult, TableProfile, TableStructure
from doc_ray.application.db_analyzer.quality import exit_code, find_issues
from doc_ray.application.db_analyzer.report import to_json, to_markdown


def _resolve_schemas(engine: Engine, conn: object, cfg: AnalyzerConfig) -> list[str]:
    """Apply the config's include/exclude schema filters to the engine's schema list.

    Args:
        engine: The connected engine.
        conn: A connection returned by ``engine.connect``.
        cfg: The resolved configuration.

    Returns:
        Schema names to inspect, in the engine's reported order.
    """
    schemas = engine.list_schemas(conn)
    if cfg.include_schemas:
        wanted = set(cfg.include_schemas)
        schemas = [s for s in schemas if s in wanted]
    if cfg.exclude_schemas:
        excluded = set(cfg.exclude_schemas)
        schemas = [s for s in schemas if s not in excluded]
    return schemas


def analyze(cfg: AnalyzerConfig) -> AnalysisResult:
    """Run one full analysis: connect, inspect, profile, derive issues.

    Args:
        cfg: The resolved configuration.

    Returns:
        The complete analysis result.

    Raises:
        ValueError: If ``cfg.engine`` names an unregistered engine.
    """
    engine = get_engine(cfg.engine)
    with engine.connect(cfg.dsn, read_only=cfg.read_only) as conn:
        database = engine.database_name(conn)
        schemas = _resolve_schemas(engine, conn, cfg)
        tables: list[TableStructure] = []
        profiles: list[TableProfile] = []
        for schema in schemas:
            for table_name in engine.list_tables(conn, schema):
                structure = engine.get_table_structure(conn, schema, table_name)
                tables.append(structure)
                if not cfg.structure_only:
                    profiles.append(
                        engine.profile_table(
                            conn,
                            schema,
                            table_name,
                            structure.columns,
                            structure.primary_key,
                            sample_threshold=cfg.sample_threshold,
                            duplicate_threshold=cfg.duplicate_threshold,
                        )
                    )
        issues = find_issues(tables, profiles)
    return AnalysisResult(
        engine=cfg.engine,
        database=database,
        schemas=schemas,
        tables=tables,
        profiles=profiles,
        issues=issues,
    )


def _split_csv(value: str | None) -> tuple[str, ...] | None:
    """Split a ``--schemas``-style CLI value into a tuple, passing ``None`` through.

    Args:
        value: The raw CLI string, or ``None`` if the flag was not given.

    Returns:
        A trimmed, non-empty tuple, or ``None``.
    """
    return None if value is None else tuple(p.strip() for p in value.split(",") if p.strip())


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser.

    Returns:
        The configured parser.
    """
    ap = argparse.ArgumentParser(description="Inspect a database's structure and profile its data.")
    ap.add_argument("--config", help="Path to a JSON config file.")
    ap.add_argument("--engine", default=None, help="Database engine (default: postgres).")
    ap.add_argument("--dsn", default=None, help="Connection string.")
    ap.add_argument("--schemas", default=None, help="Comma-separated schemas to include.")
    ap.add_argument("--exclude-schemas", default=None, help="Comma-separated schemas to skip.")
    ap.add_argument("--sample-threshold", type=int, default=None)
    ap.add_argument("--duplicate-threshold", type=int, default=None)
    ap.add_argument("--output-dir", default=None, help="Directory to write the reports to.")
    ap.add_argument(
        "--structure-only", action="store_true", default=None, help="Skip data profiling."
    )
    ap.add_argument(
        "--allow-write",
        action="store_true",
        default=None,
        help="DANGEROUS: disable the default database-enforced read-only guard.",
    )
    ap.add_argument(
        "--json", action="store_true", help="Print the JSON report instead of Markdown."
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    """Run the database analyzer CLI.

    Args:
        argv: Argument vector; defaults to ``sys.argv`` when ``None``.

    Returns:
        Process exit code: ``0`` no issues, ``2`` issues found, ``1`` on
        misconfiguration or an unreachable database.
    """
    args = _build_arg_parser().parse_args(argv)
    overrides = {
        "engine": args.engine,
        "dsn": args.dsn,
        "include_schemas": _split_csv(args.schemas),
        "exclude_schemas": _split_csv(args.exclude_schemas),
        "sample_threshold": args.sample_threshold,
        "duplicate_threshold": args.duplicate_threshold,
        "output_dir": args.output_dir,
        "structure_only": args.structure_only,
        "read_only": False if args.allow_write else None,
    }

    try:
        cfg = load_config(config_path=args.config, overrides=overrides)
    except (OSError, ValueError) as e:
        print(f"Invalid configuration: {e}", file=sys.stderr)
        return 1

    try:
        result = analyze(cfg)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    except psycopg.OperationalError as e:
        print(f"Cannot reach database: {e}", file=sys.stderr)
        return 1

    out_dir = Path(cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "db_analysis.md").write_text(to_markdown(result))
    (out_dir / "db_analysis.json").write_text(to_json(result))

    print(to_json(result) if args.json else to_markdown(result))
    print(f"\nReports written to {out_dir}/db_analysis.{{md,json}}", file=sys.stderr)
    return exit_code(result.issues)


if __name__ == "__main__":
    raise SystemExit(main())
