#!/usr/bin/env python3
"""Derive an accurate content-type for every captioned image (non-destructive).

The Tier-1 ``image_class`` heuristic is unreliable and text blocks slip through
as "figures"; the VLM caption, however, is accurate. This classifies each image's
existing caption (a fast text-only vLLM call — no re-vision) into a clean type and
stores it in ``visual_caption.analysis.content_type`` (jsonb, no schema change).
The gallery then filters real figures from text/code. Resumable: rows that already
carry a ``content_type`` are skipped.

Env: DOCRAY_VLM_ENDPOINT, DOCRAY_VLM_MODEL, CLASSIFY_CONCURRENCY (default 48),
     PRISM_WORK_DIR (log dir, default system temp).

Disclaimer: see ../DISCLAIMER.md. No statement or premise not backed by a real
logical definition or verifiable reference should be taken for granted.
"""

# ruff: noqa: E501 — long f-string progress/log lines.
from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_WORK = os.environ.get("PRISM_WORK_DIR") or os.path.join(tempfile.gettempdir(), "prism-work")
CONC = int(os.environ.get("CLASSIFY_CONCURRENCY", "48"))
ENDPOINT = os.environ.get("DOCRAY_VLM_ENDPOINT", "http://localhost:8011/v1").rstrip("/")
MODEL = os.environ.get("DOCRAY_VLM_MODEL", "qwen3-vl-8b")
TYPES = {"photo", "diagram", "chart", "table", "code", "text", "cover", "map", "other"}
_PROMPT = (
    "You label a document image using only its description. Reply with EXACTLY one word "
    "from this set: photo, diagram, chart, table, code, text, cover, map, other.\n"
    "Guidance: 'text' = a plain paragraph/page/snippet of prose; 'code' = source code or "
    "terminal output; 'diagram' = flowchart/schematic/graph; 'chart' = plot/graph of data; "
    "'photo' = a photograph; 'table' = a data table; 'cover' = a book/section cover.\n"
    'Description: "{cap}"\nWord:'
)


def _classify(caption: str) -> str:
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": _PROMPT.format(cap=caption[:1200])}],
        "max_tokens": 4,
        "temperature": 0.0,
    }
    req = urllib.request.Request(
        f"{ENDPOINT}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read())
    word = (body["choices"][0]["message"]["content"] or "").strip().lower()
    word = "".join(ch for ch in word if ch.isalpha())
    return word if word in TYPES else "other"


def _work(pool: ConnectionPool, row: dict[str, Any]) -> str:
    ctype = _classify(row["caption"])
    with pool.connection() as conn:
        conn.execute(
            "UPDATE visual_caption SET analysis = coalesce(analysis,'{}'::jsonb) "
            "|| jsonb_build_object('content_type', %s::text) WHERE id = %s",
            (ctype, row["id"]),
        )
    return ctype


def main() -> int:
    os.makedirs(_WORK, exist_ok=True)
    log = open(os.path.join(_WORK, "prism_classify.log"), "a", buffering=1)  # noqa: SIM115 — streamed for the whole run
    url = os.environ["DATABASE_URL"]
    with psycopg.connect(url, row_factory=dict_row, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT id, caption FROM visual_caption "
            "WHERE caption <> '' AND NOT (analysis ? 'content_type')"
        ).fetchall()
    header = f"\n=== classify start: {len(rows)} captions x{CONC} model={MODEL} ==="
    log.write(header + "\n")
    print(header + f"  (progress -> {_WORK}/prism_classify.log)")
    started = time.monotonic()
    counts: dict[str, int] = {}
    pool = ConnectionPool(url, min_size=4, max_size=24, kwargs={"autocommit": True})
    n = 0
    with ThreadPoolExecutor(max_workers=CONC) as ex:
        futs = [ex.submit(_work, pool, r) for r in rows]
        for fut in as_completed(futs):
            n += 1
            try:
                res = fut.result()
                counts[res] = counts.get(res, 0) + 1
            except Exception as exc:  # noqa: BLE001 — isolate per-row failures
                counts["_err"] = counts.get("_err", 0) + 1
                if counts["_err"] <= 10:
                    log.write(f"  ERR {type(exc).__name__}: {exc}\n")
            if n % 500 == 0 or n == len(rows):
                el = time.monotonic() - started
                eta = (len(rows) - n) / (n / el) / 60 if el else 0
                log.write(
                    f"[{n}/{len(rows)}] {n / el:.0f}/s eta {eta:.0f}m | {dict(sorted(counts.items()))}\n"
                )
    pool.close()
    footer = f"=== classify DONE {dict(sorted(counts.items()))} in {(time.monotonic() - started) / 60:.1f}m ==="
    log.write(footer + "\n")
    print(footer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
