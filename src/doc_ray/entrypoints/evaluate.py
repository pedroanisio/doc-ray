"""CLI entrypoint for corpus evaluation and drift snapshots."""

from __future__ import annotations

import argparse
import json

from doc_ray.adapters import persistence as db
from doc_ray.application import evaluation
from doc_ray.config import Config


def main(argv: list[str] | None = None) -> int:
    """Create one evaluation snapshot and base metric run."""
    parser = argparse.ArgumentParser(description="Record doc-ray evaluation metrics.")
    parser.add_argument("--source", default="manual", help="Snapshot source label.")
    parser.add_argument("--evaluator", default="doc-ray/evaluation", help="Evaluator name.")
    parser.add_argument("--evaluator-version", default="1", help="Evaluator version.")
    parser.add_argument("--note", help="Optional evaluation run note.")
    parser.add_argument("--json", action="store_true", help="Print created identifiers as JSON.")
    args = parser.parse_args(argv)

    cfg = Config.from_env()
    with db.connect(cfg) as conn:
        result = evaluation.create_evaluation_run(
            conn,
            source=args.source,
            evaluator=args.evaluator,
            evaluator_version=args.evaluator_version,
            note=args.note,
        )
    payload = {"snapshot_id": result.snapshot_id, "run_id": result.run_id}
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"evaluation run {result.run_id} captured snapshot {result.snapshot_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
