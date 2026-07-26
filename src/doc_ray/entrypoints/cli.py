"""Command-line entrypoint: ``python -m ingest.cli <file-dir-or-url> [...]``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from doc_ray.adapters.document_source import resolve_source
from doc_ray.adapters.extraction import supported_extensions
from doc_ray.application.pipeline import expand_paths, ingest_paths, ingest_refs
from doc_ray.config import Config


def split_refs(inputs: list[str]) -> tuple[list[str], list[str]]:
    """Split raw CLI arguments into (source refs, local path args), keeping order.

    A ref is anything a registered ``DocumentSource`` claims (e.g. an arXiv
    URL or ``arxiv:<id>``); everything else is treated as a filesystem path.
    """
    refs = [a for a in inputs if resolve_source(a) is not None]
    paths = [a for a in inputs if resolve_source(a) is None]
    return refs, paths


def main(argv: list[str] | None = None) -> int:
    """Run the ingest CLI.

    Args:
        argv: Argument vector; defaults to ``sys.argv`` when ``None``.

    Returns:
        Process exit code: ``0`` when all inputs ingested, ``1`` when nothing
        ingestible was found, ``2`` when any input failed.
    """
    parser = argparse.ArgumentParser(
        prog="ingest",
        description="Load documents into the doc-ray store (Postgres + object storage).",
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help=(
            "Files or directories to ingest (directories are searched recursively), "
            "or remote refs such as arXiv URLs / arxiv:<id>."
        ),
    )
    args = parser.parse_args(argv)

    cfg = Config.from_env()
    refs, path_args = split_refs(args.paths)
    supported = set(supported_extensions())
    files = expand_paths(path_args, supported)

    if not files and not refs:
        print(f"No supported documents found (supported: {sorted(supported)}).")
        return 1

    total = len(files) + len(refs)
    print(f"Ingesting {total} input(s) into {cfg.database_url.split('@')[-1]} ...")
    results = []
    if refs:
        results += ingest_refs(cfg, refs)
    if files:
        results += ingest_paths(cfg, files)

    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
        tag = {"ingested": "OK ", "duplicate": "DUP", "failed": "ERR"}.get(r.status, "?")
        label = r.path if "://" in r.path or r.path.startswith("arxiv:") else Path(r.path).name
        line = f"  [{tag}] {label} -> {r.document_id or '-'}"
        if r.detail:
            line += f"  ({r.detail})"
        print(line)

    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    print(f"Done: {summary}")
    return 0 if counts.get("failed", 0) == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
