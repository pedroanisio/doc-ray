#!/usr/bin/env python3
"""Read-only evidence of the Prism visual layer on whatever DATABASE_URL points at.

STRICTLY READ-ONLY: it issues SELECTs only — no INSERT/UPDATE/DELETE/DDL — so it
is safe to run against the production managed cluster. It reports the schema
version, whether the six visual tables exist, corpus-wide row/document counts per
tier, the image-class distribution, embedding dimensionality, and the Tier-3
caption status breakdown (committed vs review).

Usage (on a host whose DATABASE_URL is the target cluster):
    DATABASE_URL=... python scripts/prism_prod_evidence.py

Exit codes: 0 = visual layer present (evidence printed); 3 = not deployed yet
(migrations 089/091/093 not applied — nothing to show); 2 = usage error.
"""

from __future__ import annotations

import os
import sys

import psycopg
from psycopg.rows import dict_row

_VISUAL_TABLES = (
    "document_image",
    "image_tier1_features",
    "image_embedding",
    "sentence_vision_embedding",
    "visual_caption",
    "visual_review",
)


def _existing(conn: psycopg.Connection, tables: tuple[str, ...]) -> set[str]:
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = ANY(%s)",
        (list(tables),),
    ).fetchall()
    return {r["table_name"] for r in rows}


def _scalar(conn: psycopg.Connection, sql: str) -> object:
    row = conn.execute(sql).fetchone()
    return next(iter(row.values())) if row else None


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("fatal: DATABASE_URL is not set", file=sys.stderr)
        return 2

    with psycopg.connect(url, row_factory=dict_row, autocommit=True) as conn:
        version = _scalar(conn, "SELECT version FROM schema_version")
        print(f"\n  schema_version: {version}")
        present = _existing(conn, _VISUAL_TABLES)
        if not present:
            print("\n  Prism visual layer NOT deployed on this database.")
            print("  The visual tables do not exist — apply migrations 089-094, deploy the")
            print("  worker image, then run scripts/enqueue_visual_backfill.py.\n")
            return 3

        print(f"  visual tables present: {len(present)}/{len(_VISUAL_TABLES)}\n")
        print("  == corpus-wide counts ==")
        for table in _VISUAL_TABLES:
            if table not in present:
                print(f"    {table:28} (table missing)")
                continue
            rows = _scalar(conn, f"SELECT count(*) c FROM {table}")
            docs = _scalar(conn, f"SELECT count(DISTINCT document_id) d FROM {table}")
            print(f"    {table:28} rows={rows:<8} documents={docs}")

        if "image_tier1_features" in present:
            print("\n  == Tier-1 image-class distribution (corpus-wide) ==")
            for row in conn.execute(
                "SELECT image_class, count(*) c FROM image_tier1_features "
                "GROUP BY image_class ORDER BY c DESC"
            ).fetchall():
                print(f"    {row['image_class']:12} {row['c']}")

        if "image_embedding" in present:
            dims = _scalar(conn, "SELECT max(vector_dims(vector)) d FROM image_embedding")
            models = conn.execute(
                "SELECT model, count(*) c FROM image_embedding GROUP BY model ORDER BY c DESC"
            ).fetchall()
            print("\n  == Tier-2 embeddings ==")
            print(f"    vector dims: {dims}")
            for row in models:
                print(f"    model={row['model']} rows={row['c']}")

        if "visual_caption" in present:
            print("\n  == Tier-3 caption status (only 'committed' is trusted) ==")
            for row in conn.execute(
                "SELECT status, count(*) c, round(avg(grounding_score)::numeric, 3) avg_score "
                "FROM visual_caption GROUP BY status ORDER BY c DESC"
            ).fetchall():
                print(f"    {row['status']:10} {row['c']:<8} avg_grounding={row['avg_score']}")

        print()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
