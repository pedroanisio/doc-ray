"""span_geometry_from_layout_rows: correctness and complexity regressions.

The failure this pins: annotating book-sized documents sat for
hours in "Registering N annotation spans" and one book was terminally failed
at the execution timeout inside this exact function. Root cause was a
per-span scan of every layout row — O(spans x rows) pure-Python work, tens of
billions of iterations for a 171k-span book — plus a caller join that emitted
one duplicate copy of each layout row per overlapping staged span, inflating
both the row count and the per-page overlap scores.
"""

from __future__ import annotations

import random
import time
from typing import Any

from doc_ray.adapters.persistence import span_geometry_from_layout_rows


def _row(
    start: int,
    end: int,
    page: int,
    x0: float = 0.0,
    y0: float = 0.0,
    x1: float = 1.0,
    y1: float = 1.0,
) -> dict[str, Any]:
    return {
        "char_start": start,
        "char_end": end,
        "page_number": page,
        "bbox_x0": x0,
        "bbox_y0": y0,
        "bbox_x1": x1,
        "bbox_y1": y1,
    }


def test_picks_the_page_with_the_largest_overlap():
    rows = [_row(0, 10, 1), _row(8, 30, 2)]
    geom = span_geometry_from_layout_rows([(5, 20)], rows)
    assert geom[(5, 20)]["page_number"] == 2  # overlap 12 beats overlap 5


def test_overlap_tie_prefers_the_lower_page():
    rows = [_row(0, 10, 2), _row(10, 20, 1)]
    geom = span_geometry_from_layout_rows([(5, 15)], rows)
    assert geom[(5, 15)]["page_number"] == 1


def test_bbox_is_the_union_of_the_winning_pages_rows():
    rows = [
        _row(0, 5, 1, x0=1.0, y0=1.0, x1=2.0, y1=2.0),
        _row(5, 10, 1, x0=0.5, y0=3.0, x1=4.0, y1=5.0),
    ]
    geom = span_geometry_from_layout_rows([(0, 10)], rows)
    got = geom[(0, 10)]
    assert (got["bbox_x0"], got["bbox_y0"], got["bbox_x1"], got["bbox_y1"]) == (0.5, 1.0, 4.0, 5.0)


def test_span_without_overlapping_rows_gets_no_geometry():
    geom = span_geometry_from_layout_rows([(100, 110)], [_row(0, 10, 1)])
    assert geom == {}


def test_duplicate_rows_do_not_skew_the_page_choice():
    """The caller's join can emit one copy of a layout row per overlapping
    staged span. Accumulating those duplicates multiplies that page's overlap
    score, so a partially-overlapping page repeated often enough outvoted the
    page that actually contains the span."""
    full_match = _row(0, 10, 1)
    partial = _row(4, 10, 2)
    rows = [full_match, partial, dict(partial), dict(partial)]  # join artifacts
    geom = span_geometry_from_layout_rows([(0, 10)], rows)
    assert geom[(0, 10)]["page_number"] == 1  # overlap 10 must beat a real 6


def _brute_force(
    pairs: list[tuple[int, int]], rows: list[dict[str, Any]]
) -> dict[tuple[int, int], dict[str, Any]]:
    """The original per-span full scan, kept as the semantic reference."""
    out: dict[tuple[int, int], dict[str, Any]] = {}
    for start, end in pairs:
        by_page: dict[int, dict[str, Any]] = {}
        for row in rows:
            overlap = min(end, int(row["char_end"])) - max(start, int(row["char_start"]))
            if overlap <= 0:
                continue
            page = int(row["page_number"])
            g = by_page.setdefault(
                page,
                {
                    "overlap": 0,
                    "page_number": page,
                    "bbox_x0": float(row["bbox_x0"]),
                    "bbox_y0": float(row["bbox_y0"]),
                    "bbox_x1": float(row["bbox_x1"]),
                    "bbox_y1": float(row["bbox_y1"]),
                },
            )
            g["overlap"] += overlap
            g["bbox_x0"] = min(g["bbox_x0"], float(row["bbox_x0"]))
            g["bbox_y0"] = min(g["bbox_y0"], float(row["bbox_y0"]))
            g["bbox_x1"] = max(g["bbox_x1"], float(row["bbox_x1"]))
            g["bbox_y1"] = max(g["bbox_y1"], float(row["bbox_y1"]))
        if by_page:
            best = max(by_page.values(), key=lambda g: (g["overlap"], -g["page_number"]))
            out[(start, end)] = {
                k: best[k] for k in ("page_number", "bbox_x0", "bbox_y0", "bbox_x1", "bbox_y1")
            }
    return out


def test_matches_the_reference_scan_on_random_duplicate_free_input():
    rng = random.Random(20260712)
    rows = []
    cursor = 0
    for _ in range(400):
        length = rng.randint(1, 40)
        # Mostly ordered runs with occasional overlaps/gaps, like real layout.
        cursor += rng.randint(-5, 15)
        cursor = max(0, cursor)
        rows.append(_row(cursor, cursor + length, rng.randint(1, 12)))
        cursor += length
    pairs = []
    for _ in range(300):
        s = rng.randint(0, cursor)
        pairs.append((s, s + rng.randint(1, 60)))
    pairs = sorted(set(pairs))
    assert span_geometry_from_layout_rows(pairs, rows) == _brute_force(pairs, rows)


def test_large_documents_complete_in_loglinear_time_not_quadratic():
    """171k spans x a book's layout rows must be seconds, not hours.

    12k x 12k here: the removed O(n*m) scan needs ~144M dict-heavy iterations
    (a minute and up); the windowed version does a few thousand bisects over
    tight candidate windows. The 5s budget has huge headroom for slow CI, but
    the quadratic version cannot come close to it.
    """
    rows = [_row(i * 10, i * 10 + 9, (i // 40) + 1) for i in range(12_000)]
    pairs = [(i * 10 + 2, i * 10 + 7) for i in range(12_000)]
    t0 = time.monotonic()
    geom = span_geometry_from_layout_rows(pairs, rows)
    elapsed = time.monotonic() - t0
    assert len(geom) == 12_000
    assert elapsed < 5.0, f"geometry took {elapsed:.1f}s — the hot loop is back"
