"""Command-line entrypoint: python -m ingest.cli <file-or-dir> [...]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config
from .extract import _EXTRACTORS
from .pipeline import expand_paths, ingest_paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ingest",
        description="Load documents into the doc-ray store (Postgres + object storage).",
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="Files or directories to ingest (directories are searched recursively).",
    )
    args = parser.parse_args(argv)

    cfg = Config.from_env()
    supported = set(_EXTRACTORS.keys())
    files = expand_paths(args.paths, supported)

    if not files:
        print(f"No supported documents found (supported: {sorted(supported)}).")
        return 1

    print(f"Ingesting {len(files)} file(s) into {cfg.database_url.split('@')[-1]} ...")
    results = ingest_paths(cfg, files)

    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
        tag = {"ingested": "OK ", "duplicate": "DUP", "failed": "ERR"}.get(r.status, "?")
        line = f"  [{tag}] {Path(r.path).name} -> {r.document_id or '-'}"
        if r.detail:
            line += f"  ({r.detail})"
        print(line)

    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    print(f"Done: {summary}")
    return 0 if counts.get("failed", 0) == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
