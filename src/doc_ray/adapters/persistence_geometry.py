"""Document geometry persistence: layout runs, page metrics, span bboxes.

Split out of :mod:`doc_ray.adapters.persistence` (D-015). Bridges extractor
geometry (zero-based pages, PyMuPDF bboxes) to doc-ray's durable
representation (1-based pages, half-open character spans), including the
BL-133 log-linear span-geometry pass.
"""

from __future__ import annotations

import bisect
from typing import Any

import psycopg

#: A psycopg connection whose rows are mappings (``row_factory=dict_row``);
#: mirrors :data:`doc_ray.adapters.persistence.DictConnection` (import cycle).
DictConnection = psycopg.Connection[dict[str, Any]]


def layout_runs_from_page_runs(
    full_text: str, page_runs: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """Convert extracted PDF layout runs into durable text-offset rows.

    ``Extracted.page_runs`` is intentionally extractor-local: PyMuPDF reports
    zero-based pages and geometry, while the rest of doc-ray addresses text by
    half-open character spans. This helper bridges the two representations by
    finding each extracted line in the final persisted ``full_text``.
    """
    if not full_text or not page_runs:
        return []

    rows: list[dict[str, Any]] = []
    cursor = 0
    for run in page_runs:
        text = str(run.get("text") or "")
        if not text:
            continue
        bbox = run.get("bbox")
        if bbox is None or len(bbox) != 4:
            continue
        start = full_text.find(text, cursor)
        if start < 0:
            start = full_text.find(text)
        if start < 0:
            continue
        end = start + len(text)
        rows.append(
            {
                "ordinal": len(rows),
                "page_number": int(run.get("page", 0)) + 1,
                "page_width": float(run["page_width"]) if run.get("page_width") else None,
                "page_height": float(run["page_height"]) if run.get("page_height") else None,
                "char_start": start,
                "char_end": end,
                "bbox_x0": float(bbox[0]),
                "bbox_y0": float(bbox[1]),
                "bbox_x1": float(bbox[2]),
                "bbox_y1": float(bbox[3]),
                "text": text,
            }
        )
        cursor = end
    return rows


def page_rows_from_page_metrics(
    page_metrics: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Map zero-based extracted page metrics to database page rows."""
    rows: list[dict[str, Any]] = []
    for metric in page_metrics or []:
        page_width = float(metric["page_width"])
        page_height = float(metric["page_height"])
        text_run_count = int(metric.get("text_run_count") or 0)
        rows.append(
            {
                "page_number": int(metric["page"]) + 1,
                "page_width": page_width,
                "page_height": page_height,
                "has_text": bool(metric.get("has_text")) or text_run_count > 0,
                "text_run_count": text_run_count,
            }
        )
    return rows


def span_geometry_from_layout_rows(
    pairs: list[tuple[int, int]], layout_rows: list[dict[str, Any]]
) -> dict[tuple[int, int], dict[str, Any]]:
    """Return the best page/bbox geometry for each requested span range.

    Scanning every layout row per span is O(spans x rows) — for a book-sized
    document (171k spans) that is tens of billions of pure-Python iterations,
    which is what kept annotate jobs in "Registering annotation spans" for
    hours until they hit the execution timeout (2026-07-12 incident). Rows are
    deduplicated (the caller's join can emit one copy per overlapping staged
    span, which also skewed the per-page overlap vote), sorted once, and each
    span visits only its bisected candidate window.
    """
    out: dict[tuple[int, int], dict[str, Any]] = {}
    if not pairs or not layout_rows:
        return out
    rows = sorted(
        {
            (
                int(row["char_start"]),
                int(row["char_end"]),
                int(row["page_number"]),
                float(row["bbox_x0"]),
                float(row["bbox_y0"]),
                float(row["bbox_x1"]),
                float(row["bbox_y1"]),
            )
            for row in layout_rows
        }
    )
    starts = [r[0] for r in rows]
    # Nondecreasing running maximum of char_end: the first index whose value
    # exceeds a span's start bounds the candidate window from the left even
    # when individual row ends are not monotonic.
    max_end_so_far: list[int] = []
    running = rows[0][1]
    for _, row_end, *_ in rows:
        running = max(running, row_end)
        max_end_so_far.append(running)
    for start, end in pairs:
        hi = bisect.bisect_left(starts, end)  # rows with char_start < end
        lo = bisect.bisect_right(max_end_so_far, start, 0, hi)  # first with end > start
        by_page: dict[int, dict[str, Any]] = {}
        for i in range(lo, hi):
            row_start, row_end, page, x0, y0, x1, y1 = rows[i]
            overlap = min(end, row_end) - max(start, row_start)
            if overlap <= 0:
                continue
            page_geom = by_page.get(page)
            if page_geom is None:
                by_page[page] = {
                    "overlap": overlap,
                    "page_number": page,
                    "bbox_x0": x0,
                    "bbox_y0": y0,
                    "bbox_x1": x1,
                    "bbox_y1": y1,
                }
                continue
            page_geom["overlap"] += overlap
            page_geom["bbox_x0"] = min(page_geom["bbox_x0"], x0)
            page_geom["bbox_y0"] = min(page_geom["bbox_y0"], y0)
            page_geom["bbox_x1"] = max(page_geom["bbox_x1"], x1)
            page_geom["bbox_y1"] = max(page_geom["bbox_y1"], y1)
        if by_page:
            best = max(by_page.values(), key=lambda g: (g["overlap"], -g["page_number"]))
            out[(start, end)] = {
                "page_number": best["page_number"],
                "bbox_x0": best["bbox_x0"],
                "bbox_y0": best["bbox_y0"],
                "bbox_x1": best["bbox_x1"],
                "bbox_y1": best["bbox_y1"],
            }
    return out


def replace_document_layout_runs(
    conn: DictConnection,
    document_id: str,
    full_text: str | None,
    page_runs: list[dict[str, Any]] | None,
) -> None:
    """Replace the persisted layout-run index for one document."""
    conn.execute("DELETE FROM document_layout_runs WHERE document_id=%s", (document_id,))
    rows = layout_runs_from_page_runs(full_text or "", page_runs)
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO document_layout_runs
                (document_id, ordinal, page_number, page_width, page_height, char_start, char_end,
                 bbox_x0, bbox_y0, bbox_x1, bbox_y1, text)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (document_id, ordinal) DO UPDATE SET
                page_number = EXCLUDED.page_number,
                page_width = EXCLUDED.page_width,
                page_height = EXCLUDED.page_height,
                char_start = EXCLUDED.char_start,
                char_end = EXCLUDED.char_end,
                bbox_x0 = EXCLUDED.bbox_x0,
                bbox_y0 = EXCLUDED.bbox_y0,
                bbox_x1 = EXCLUDED.bbox_x1,
                bbox_y1 = EXCLUDED.bbox_y1,
                text = EXCLUDED.text
            """,
            [
                (
                    document_id,
                    row["ordinal"],
                    row["page_number"],
                    row["page_width"],
                    row["page_height"],
                    row["char_start"],
                    row["char_end"],
                    row["bbox_x0"],
                    row["bbox_y0"],
                    row["bbox_x1"],
                    row["bbox_y1"],
                    row["text"],
                )
                for row in rows
            ],
        )


def replace_document_pages(
    conn: DictConnection,
    document_id: str,
    page_metrics: list[dict[str, Any]] | None,
) -> None:
    """Replace authoritative per-page geometry for one document."""
    conn.execute("DELETE FROM document_pages WHERE document_id=%s", (document_id,))
    rows = page_rows_from_page_metrics(page_metrics)
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO document_pages
                (document_id, page_number, page_width, page_height, has_text, text_run_count)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (document_id, page_number) DO UPDATE SET
                page_width = EXCLUDED.page_width,
                page_height = EXCLUDED.page_height,
                has_text = EXCLUDED.has_text,
                text_run_count = EXCLUDED.text_run_count
            """,
            [
                (
                    document_id,
                    row["page_number"],
                    row["page_width"],
                    row["page_height"],
                    row["has_text"],
                    row["text_run_count"],
                )
                for row in rows
            ],
        )


def _update_span_geometry_from_layout(
    conn: DictConnection, document_id: str, pairs: list[tuple[int, int]]
) -> None:
    """Populate page/bbox fields for staged spans from persisted layout runs."""
    if not pairs:
        return
    # No staged-span filter in SQL: any correlated range predicate against the
    # unindexed _span_stage temp table degenerates to a nested loop —
    # O(rows x spans) inside the planner (the JOIN variant additionally
    # duplicated each row per overlapping span). The geometry pass is
    # log-linear and deduplicates, so fetch the document's runs once — bounded
    # by document size — and let it discard the non-overlapping rows.
    layout_rows = conn.execute(
        """
        SELECT page_number, char_start, char_end,
               bbox_x0, bbox_y0, bbox_x1, bbox_y1
        FROM document_layout_runs
        WHERE document_id=%s
        """,
        (document_id,),
    ).fetchall()
    geometry = span_geometry_from_layout_rows(pairs, layout_rows)
    if not geometry:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            UPDATE spans
               SET page_number=%s,
                   bbox_x0=%s,
                   bbox_y0=%s,
                   bbox_x1=%s,
                   bbox_y1=%s
             WHERE document_id=%s AND char_start=%s AND char_end=%s
            """,
            [
                (
                    geom["page_number"],
                    geom["bbox_x0"],
                    geom["bbox_y0"],
                    geom["bbox_x1"],
                    geom["bbox_y1"],
                    document_id,
                    start,
                    end,
                )
                for (start, end), geom in geometry.items()
            ],
        )
