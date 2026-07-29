#!/usr/bin/env python3
"""Generate a per-corpus / per-document breakdown dashboard as standalone HTML.

Reports every artifact doc-ray holds for every document — text, sentences,
tokens, lexeme occurrences, NLP annotation layers, sentence embeddings, linked
entities, lens projections, images, image embeddings and committed captions —
plus corpus totals and per-artifact coverage.

    DATABASE_URL=... python scripts/gen_corpus_dashboard.py
    DATABASE_URL=... python scripts/gen_corpus_dashboard.py --out /tmp/corpus.html --json

Reads only. Assumes the ``docray_batch`` role (RLS would otherwise report an
empty corpus) and refuses to run if the corpus is invisible. Roughly 20s of
queries against the production cluster; every aggregate is either a rollup read
or a measured-bounded join — see ``_corpus_dashboard_data`` for what is counted
and, more importantly, what is not.

Exit codes: 0 written; 1 usage/precondition failure; 2 a query failed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import _corpus_dashboard_data as data
import _corpus_dashboard_render as render

#: Artifact families whose per-document counts need a join through `spans`; the
#: reason the run costs seconds rather than milliseconds.
SLOW_NOTE = (
    "Per-document entity counts are linked entities (entity_link), not raw mentions: "
    "entity_mention is keyed by span_id with no document_id index, so counting it per "
    "document requires scanning the 89M-row spans table and does not finish within 90s "
    "on the live cluster. Images are absent from text-only documents by nature, so a "
    "low image coverage is not by itself a gap."
)


def _pairs(conn: Any, sql: str) -> list[tuple[Any, ...]]:
    """Run a read-only query and return its rows as tuples."""
    return [tuple(r) for r in conn.execute(sql).fetchall()]


def fetch(conn: Any) -> dict[str, Any]:
    """Pull every artifact map the report needs, in one pass per family."""
    documents = [
        {
            "document_id": r[0],
            "title": r[1],
            "doc_type": r[2],
            "status": r[3],
            "language": r[4],
            "page_count": r[5],
            "char_count": r[6],
            "byte_size": r[7],
            "created_at": r[8],
        }
        for r in _pairs(conn, data.DOCUMENTS_SQL)
    ]

    layers: dict[str, set[str]] = {}
    for did, layer in _pairs(conn, data.LAYERS_SQL):
        layers.setdefault(did, set()).add(layer)

    embeddings: dict[str, dict[str, int]] = {}
    for did, model, n in _pairs(conn, data.EMBEDDINGS_SQL):
        embeddings.setdefault(did, {})[model] = n

    captions: dict[str, dict[str, int]] = {}
    for did, status, n in _pairs(conn, data.CAPTIONS_SQL):
        captions.setdefault(did, {})[status] = n

    n_images = dict(_pairs(conn, data.IMAGES_SQL))
    n_image_emb = dict(_pairs(conn, data.IMAGE_EMBEDDINGS_SQL))
    n_vision = dict(_pairs(conn, data.VISION_SENTENCES_SQL))
    images = {
        did: (n_images.get(did, 0), n_image_emb.get(did, 0), n_vision.get(did, 0))
        for did in set(n_images) | set(n_image_emb) | set(n_vision)
    }

    return {
        "documents": documents,
        "stats": {r[0]: (r[1], r[2], r[3]) for r in _pairs(conn, data.STATS_SQL)},
        "layers": layers,
        "embeddings": embeddings,
        "images": images,
        "captions": captions,
        "entity_links": dict(_pairs(conn, data.ENTITY_LINKS_SQL)),
        "projections": {r[0]: (r[1], r[2]) for r in _pairs(conn, data.PROJECTIONS_SQL)},
        "classes": {r[0]: (r[1], r[2], r[3]) for r in _pairs(conn, data.CLASSES_SQL)},
    }


def build_report(conn: Any) -> data.Report:
    """Gather, assemble, and time the whole report."""
    started = time.monotonic()
    fetched = fetch(conn)
    lex_row = conn.execute(data.CORPUS_SQL).fetchone()
    elapsed = time.monotonic() - started

    rows = data.build_rows(fetched.pop("documents"), **fetched)
    schema = conn.execute("SELECT docray_schema_version()").fetchone()
    return data.Report(
        rows=rows,
        totals=data.totals(rows, n_distinct_lexemes=lex_row[0] if lex_row else 0),
        coverage=data.coverage(rows),
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        schema_version=str(schema[0]) if schema else "unknown",
        query_seconds=elapsed,
        slow_note=SLOW_NOTE,
        by_type=dict(Counter(r.doc_type for r in rows)),
        by_status=dict(Counter(r.status for r in rows)),
    )


def as_json(report: data.Report) -> str:
    """Serialize the report so it can be diffed between runs."""
    return json.dumps(
        {
            "generated_at": report.generated_at,
            "schema_version": report.schema_version,
            "totals": report.totals.__dict__,
            "coverage": [
                {
                    "label": c.label,
                    "present": c.n_present,
                    "missing": c.n_missing,
                    "total": c.n_total,
                    "pct": round(c.pct, 2),
                }
                for c in report.coverage
            ],
            "by_type": report.by_type,
            "by_status": report.by_status,
            "documents": [
                {k: sorted(v) if isinstance(v, frozenset) else v for k, v in r.__dict__.items()}
                for r in report.rows
            ],
        },
        indent=2,
    )


def main(argv: list[str] | None = None) -> int:
    """Render the dashboard to a file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("_ephemeral/corpus-dashboard.html"))
    parser.add_argument("--json", type=Path, help="Also write the report as JSON.")
    parser.add_argument(
        "--fragment",
        action="store_true",
        help="Emit body content only, for a host that supplies its own document skeleton.",
    )
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("fatal: DATABASE_URL is not set", file=sys.stderr)
        return 1

    try:
        conn = data.connect(database_url)
    except Exception as exc:  # noqa: BLE001 - a precondition failure must be legible
        print(f"fatal: {exc}", file=sys.stderr)
        return 1
    try:
        report = build_report(conn)
    except Exception as exc:  # noqa: BLE001 - surface the failing query, do not traceback
        print(f"fatal: query failed: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    emit = render.render_body if args.fragment else render.render
    args.out.write_text(emit(report), encoding="utf-8")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(as_json(report), encoding="utf-8")

    t = report.totals
    print(
        f"wrote {args.out} — {t.n_documents} documents, {t.n_sentences:,} sentences, "
        f"{t.n_tokens:,} tokens, {t.n_embeddings:,} embeddings ({report.query_seconds:.1f}s)"
    )
    for entry in report.coverage:
        print(f"  {entry.label:<24} {entry.n_present:>5}/{entry.n_total} ({entry.pct:5.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
