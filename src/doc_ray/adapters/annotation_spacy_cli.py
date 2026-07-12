"""CLI for the spaCy annotator (split from annotation_spacy.py, D-015 cap).

The adapter module holds the pipeline/persistence machinery; this module owns
argument parsing and the batch-run loop. ``entrypoints.annotate_spacy`` is the
canonical ``python -m`` surface and imports :func:`main` from here.
"""

from __future__ import annotations

import argparse
import sys

from doc_ray.adapters import persistence as db
from doc_ray.adapters.annotation_spacy import SpacyAnnotator
from doc_ray.adapters.persistence import DictConnection
from doc_ray.config import Config


def _target_ids(conn: DictConnection, args: argparse.Namespace) -> list[str]:
    """Resolve CLI selectors to an ordered list of document ids."""
    if args.id:
        return [args.id]
    if args.filename:
        rows = conn.execute(
            "SELECT id FROM documents WHERE original_filename=%s", (args.filename,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id FROM documents WHERE status='extracted' ORDER BY created_at"
        ).fetchall()
    return [str(r["id"]) for r in rows]


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: annotate the selected documents with spaCy.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv``).

    Returns:
        Process exit code (0 on success, 1 when no documents match).
    """
    ap = argparse.ArgumentParser(description="Annotate documents with spaCy (tokens/deps/NER/...).")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--id")
    g.add_argument("--filename")
    g.add_argument("--all", action="store_true")
    args = ap.parse_args(argv)

    ann = SpacyAnnotator()
    with db.connect(Config.from_env()) as conn:
        ids = _target_ids(conn, args)
        if not ids:
            print("No documents.", file=sys.stderr)
            return 1
        for doc_id in ids:
            title_row = conn.execute(
                "SELECT left(coalesce(original_filename,title),36) t FROM documents WHERE id=%s",
                (doc_id,),
            ).fetchone()
            assert title_row is not None  # doc_id came from a documents query
            title = title_row["t"]
            try:
                r = ann.annotate(conn, doc_id)
                print(
                    f"  {title:36}  {r.get('tokens', 0):>6} tok  "
                    f"{r.get('entities', 0):>4} ent  {r.get('chunks', 0):>4} chunk  "
                    f"{r.get('coref_chains', 0):>3} coref  [{r.get('tool', '-')}]"
                )
            except Exception as e:
                print(f"  {title:36}  SKIPPED: {type(e).__name__}: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
