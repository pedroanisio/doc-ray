#!/usr/bin/env python3
"""Real-time Prism visual-layer stats, straight from the database.

STRICTLY READ-ONLY: it issues SELECTs only (no INSERT/UPDATE/DELETE/DDL), so it
is safe to run against the production cluster while a backfill is in flight. It
refreshes on an interval and shows, per tier, live totals plus the per-second
rate since the previous refresh — a lightweight console dashboard for watching
the corpus get mapped (Tier-1) and described (Tier-3).

Usage (on a host whose DATABASE_URL points at the target cluster):
    DATABASE_URL=... python scripts/prism_live_stats.py               # refresh every 5s
    DATABASE_URL=... python scripts/prism_live_stats.py --interval 10
    DATABASE_URL=... python scripts/prism_live_stats.py --once        # single snapshot

Exit: Ctrl-C. Exit codes: 0 normal, 2 usage (DATABASE_URL unset), 3 not deployed.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

_VISUAL_TABLES = ("document_image", "image_tier1_features", "visual_caption")


def _scalar(conn: psycopg.Connection[Any], sql: str) -> Any:
    row = conn.execute(sql).fetchone()
    return next(iter(row.values())) if row else None


def _deployed(conn: psycopg.Connection[Any]) -> bool:
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name = ANY(%s)",
        (list(_VISUAL_TABLES),),
    ).fetchall()
    return len({r["table_name"] for r in rows}) == len(_VISUAL_TABLES)


def _snapshot(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    """One read-only sample of every counter we display."""
    snap: dict[str, Any] = {}
    snap["documents"] = _scalar(conn, "SELECT count(*) FROM documents")
    snap["eligible"] = _scalar(
        conn,
        "SELECT count(*) FROM documents WHERE (doc_type='pdf' OR mime_type='application/pdf') "
        "AND status IN ('extracted','failed')",
    )
    snap["mapped_docs"] = _scalar(conn, "SELECT count(DISTINCT document_id) FROM document_image")
    snap["images"] = _scalar(conn, "SELECT count(*) FROM document_image")
    snap["by_source"] = {
        r["source"]: r["c"]
        for r in conn.execute(
            "SELECT source, count(*) c FROM document_image GROUP BY source ORDER BY c DESC"
        ).fetchall()
    }
    snap["features"] = _scalar(conn, "SELECT count(*) FROM image_tier1_features")
    snap["by_class"] = {
        r["image_class"]: r["c"]
        for r in conn.execute(
            "SELECT image_class, count(*) c FROM image_tier1_features "
            "GROUP BY image_class ORDER BY c DESC"
        ).fetchall()
    }
    snap["captions"] = _scalar(conn, "SELECT count(*) FROM visual_caption")
    snap["cap_docs"] = _scalar(conn, "SELECT count(DISTINCT document_id) FROM visual_caption")
    snap["by_status"] = {
        r["status"]: {"c": r["c"], "avg": r["avg"]}
        for r in conn.execute(
            "SELECT status, count(*) c, round(avg(grounding_score)::numeric, 3) avg "
            "FROM visual_caption GROUP BY status ORDER BY c DESC"
        ).fetchall()
    }
    return snap


def _rate(cur: int, prev: int | None, dt: float) -> str:
    if prev is None or dt <= 0:
        return ""
    delta = cur - prev
    if delta == 0:
        return "  (·)"
    return f"  (+{delta}, {delta / dt:.1f}/s)"


def _render(snap: dict[str, Any], prev: dict[str, Any] | None, dt: float, started: float) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    elapsed = int(time.monotonic() - started)
    el = f"{elapsed // 3600}:{elapsed % 3600 // 60:02d}:{elapsed % 60:02d}"
    p = prev or {}
    md, elig, imgs, caps = snap["mapped_docs"], snap["eligible"], snap["images"], snap["captions"]
    mapped_pct = 100 * md / elig if elig else 0
    img_rate = _rate(imgs, p.get("images"), dt)
    cap_rate = _rate(caps, p.get("captions"), dt)
    lines = [
        f"Prism live stats — {now}   elapsed {el}   Δ{dt:.1f}s",
        "",
        "CORPUS",
        f"  documents            {snap['documents']:>8,}",
        f"  eligible PDFs        {elig:>8,}",
        f"  mapped (has images)  {md:>8,} / {elig:,}  ({mapped_pct:.1f}%)",
        "",
        f"TIER-1  document_image   {imgs:>8,}{img_rate}",
    ]
    for src in ("embedded-raster", "rendered-region", "full-page"):
        lines.append(f"    {src:<18} {snap['by_source'].get(src, 0):>8,}")
    lines.append(f"    tier1 features   {snap['features']:>8,}")
    if snap["by_class"]:
        classes = "  ".join(f"{k} {v:,}" for k, v in snap["by_class"].items())
        lines.append(f"    class: {classes}")
    lines += [
        "",
        f"TIER-3  visual_caption   {caps:>8,}{cap_rate}",
        f"    documents described {snap['cap_docs']:>8,}",
    ]
    for status in ("committed", "review", "rejected"):
        st = snap["by_status"].get(status)
        if st:
            lines.append(f"    {status:<18} {st['c']:>8,}   avg_grounding {st['avg']}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Real-time Prism visual-layer stats (read-only).")
    parser.add_argument("--interval", type=float, default=5.0, help="refresh seconds (default 5)")
    parser.add_argument("--once", action="store_true", help="print one snapshot and exit")
    args = parser.parse_args()

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("fatal: DATABASE_URL is not set", file=sys.stderr)
        return 2

    is_tty = sys.stdout.isatty()
    started = time.monotonic()
    prev: dict[str, Any] | None = None
    prev_t = started
    with psycopg.connect(url, row_factory=dict_row, autocommit=True) as conn:
        if not _deployed(conn):
            print("Prism visual layer not deployed on this database.", file=sys.stderr)
            return 3
        try:
            while True:
                now = time.monotonic()
                snap = _snapshot(conn)
                out = _render(snap, prev, now - prev_t, started)
                if is_tty and not args.once:
                    sys.stdout.write("\033[2J\033[H")  # clear + home
                print(out, flush=True)
                if args.once:
                    return 0
                prev, prev_t = snap, now
                if not is_tty:
                    print("-" * 60, flush=True)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n(stopped)", file=sys.stderr)
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
