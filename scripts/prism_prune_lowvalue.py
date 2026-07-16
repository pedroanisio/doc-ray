#!/usr/bin/env python3
"""Prune non-content visual crops from the corpus (page slivers, rules, blanks).

Applies the SAME content-quality gate as extraction
(:func:`doc_ray.application.visual_features.is_meaningful_region`) to already-
stored rows, using the persisted Tier-1 features — so a re-extract and this
cleanup agree exactly. Deleting a ``document_image`` cascades to its Tier-1
features, embeddings, captions, and review rows.

DRY-RUN by default (counts only). Pass ``--apply`` to delete. Connects as the
table owner. This is the one destructive tool in the Prism set — everything else
is read-only.

Usage:
    DATABASE_URL=... python scripts/prism_prune_lowvalue.py            # dry run
    DATABASE_URL=... python scripts/prism_prune_lowvalue.py --apply    # delete
"""

# ruff: noqa: E501 — inline SQL strings carry inherently long lines.
from __future__ import annotations

import os
import sys

import psycopg
from psycopg.rows import dict_row

from doc_ray.application.visual_features import (
    BLANK_CONTRAST_MAX,
    BLANK_EDGE_MAX,
    MAX_MEANINGFUL_ASPECT,
    MIN_MEANINGFUL_SIDE_PX,
)

# Mirror is_meaningful_region() over the persisted columns. A row is junk if it is
# too small, a hairline sliver, or blank (near-zero contrast AND edge density).
_JUNK_WHERE = f"""
    di.id IN (
        SELECT di.id FROM document_image di
        JOIN image_tier1_features t ON t.document_image_id = di.id
        WHERE LEAST(di.width_px, di.height_px) < {MIN_MEANINGFUL_SIDE_PX}
           OR GREATEST(di.width_px, di.height_px)::float
                / NULLIF(LEAST(di.width_px, di.height_px), 0) > {MAX_MEANINGFUL_ASPECT}
           OR (t.contrast < {BLANK_CONTRAST_MAX} AND t.edge_density < {BLANK_EDGE_MAX})
    )
"""


def main() -> int:
    apply = "--apply" in sys.argv[1:]
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("fatal: DATABASE_URL is not set", file=sys.stderr)
        return 2

    with psycopg.connect(url, row_factory=dict_row, autocommit=True) as conn:
        total = conn.execute("SELECT count(*) n FROM document_image").fetchone()["n"]
        junk = conn.execute(
            f"SELECT count(*) n FROM document_image di WHERE {_JUNK_WHERE}"
        ).fetchone()["n"]
        caps = conn.execute(
            f"SELECT count(*) n FROM visual_caption vc WHERE vc.document_image_id IN "
            f"(SELECT di.id FROM document_image di WHERE {_JUNK_WHERE})"
        ).fetchone()["n"]
        print(f"\n  total images:        {total:,}")
        print(f"  low-value to prune:  {junk:,}  ({100 * junk / total:.0f}%)")
        print(f"  captions cascaded:   {caps:,}")
        print(f"  images remaining:    {total - junk:,}")
        print("\n  == breakdown ==")
        for row in conn.execute(
            "SELECT di.source, "
            f"count(*) FILTER (WHERE LEAST(di.width_px,di.height_px) < {MIN_MEANINGFUL_SIDE_PX}) tiny, "
            "count(*) FILTER (WHERE GREATEST(di.width_px,di.height_px)::float"
            f"/NULLIF(LEAST(di.width_px,di.height_px),0) > {MAX_MEANINGFUL_ASPECT}) sliver, "
            f"count(*) FILTER (WHERE t.contrast < {BLANK_CONTRAST_MAX} AND t.edge_density < {BLANK_EDGE_MAX}) blank "
            "FROM document_image di JOIN image_tier1_features t ON t.document_image_id=di.id "
            "GROUP BY di.source ORDER BY di.source"
        ).fetchall():
            print(
                f"    {row['source']:16} tiny={row['tiny']:,} sliver={row['sliver']:,} blank={row['blank']:,}"
            )

        if not apply:
            print("\n  DRY RUN — no rows deleted. Re-run with --apply to prune.\n")
            return 0

        with conn.transaction():
            deleted = conn.execute(f"DELETE FROM document_image di WHERE {_JUNK_WHERE}").rowcount
        print(
            f"\n  DELETED {deleted:,} low-value images (features/embeddings/captions cascaded).\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
