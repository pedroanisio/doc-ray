"""CLI entrypoint for deterministic semantic enrichment layers."""

from __future__ import annotations

import argparse
import sys

from doc_ray.adapters import persistence as db
from doc_ray.adapters.persistence import DictConnection
from doc_ray.application import semantic_enrichment
from doc_ray.config import Config


def _target_ids(conn: DictConnection, *, doc_id: str | None, all_docs: bool) -> list[str]:
    if doc_id:
        return [doc_id]
    if all_docs:
        return [
            str(row["id"])
            for row in conn.execute(
                "SELECT id FROM documents WHERE status='extracted' ORDER BY created_at"
            ).fetchall()
        ]
    return []


def main(argv: list[str] | None = None) -> int:
    """Annotate selected documents with deterministic semantic layers."""
    parser = argparse.ArgumentParser(description="Annotate deterministic semantic layers.")
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--id")
    selector.add_argument("--all", action="store_true")
    args = parser.parse_args(argv)

    cfg = Config.from_env()
    with db.connect(cfg) as conn:
        ids = _target_ids(conn, doc_id=args.id, all_docs=args.all)
        if not ids:
            print("No matching documents.", file=sys.stderr)
            return 1
        totals = {
            "discourse_relations": 0,
            "timexes": 0,
            "events": 0,
            "sentiments": 0,
            "embeddings": 0,
        }
        for doc_id in ids:
            summary = semantic_enrichment.annotate_document(conn, doc_id)
            for key, value in summary.items():
                totals[key] += value
            print(
                f"{doc_id}: discourse={summary['discourse_relations']} "
                f"timex={summary['timexes']} events={summary['events']} "
                f"sentiment={summary['sentiments']} embeddings={summary['embeddings']}"
            )
    print(
        "Done: "
        f"discourse={totals['discourse_relations']} timex={totals['timexes']} "
        f"events={totals['events']} sentiment={totals['sentiments']} "
        f"embeddings={totals['embeddings']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
