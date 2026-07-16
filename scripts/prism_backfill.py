#!/usr/bin/env python3
"""Direct, parallel Prism backfill — run the visual pipeline against prod without
the job queue (for when the deployed worker image lags the code).

Runs the visual code from this checkout directly against the prod DB + object
store. ``docray`` owns the visual tables and RLS is not forced for the owner, so
these writes behave exactly like the worker's. A process pool spreads the
CPU-bound work across cores instead of pegging one.

    --phase extract : Tier-1 — map every image/illustration/diagram (embedded
                      rasters + vector-figure clusters + full-page renders).
    --phase caption : Tier-3 — describe each image with the configured VLM,
                      grounded against page text + crop OCR, and gated (PALS).
                      NOTE: for large corpora prefer scripts/prism_caption.py,
                      which fans out at the image level to saturate a vLLM GPU;
                      this doc-granular phase is the queue-free fallback.

Env: FULL_PASS_WORKERS (default 16 extract / 4 caption), FULL_PASS_WATCHDOG
(per-doc seconds, default 900), DOCRAY_VLM_BACKEND=vllm to use the vLLM adapter,
PRISM_WORK_DIR (log/checkpoint dir, default system temp). Resumable: finished doc
ids are checkpointed and skipped; a per-doc watchdog skips a pathological doc so
one never stalls the pass.

Disclaimer: see ../DISCLAIMER.md. No statement or premise not backed by a real
logical definition or verifiable reference should be taken for granted.
"""

# ruff: noqa: E501 — long f-string progress/log lines.
from __future__ import annotations

import multiprocessing as mp
import os
import signal
import sys
import tempfile
import time
from typing import Any

import psycopg
from psycopg.rows import dict_row

_WORK = os.environ.get("PRISM_WORK_DIR") or os.path.join(tempfile.gettempdir(), "prism-work")
WATCHDOG_SECONDS = int(os.environ.get("FULL_PASS_WATCHDOG", "900"))

_G: dict[str, Any] = {}


class _DocTimeout(Exception):
    pass


def _on_alarm(signum: int, frame: object) -> None:
    raise _DocTimeout()


def _init_worker(phase: str) -> None:
    """Per-process setup: one DB connection + adapters, reused for every doc."""
    from doc_ray.adapters.object_store import ObjectStore
    from doc_ray.config import Config

    _G["phase"] = phase
    _G["store"] = ObjectStore(Config.from_env())
    _G["conn"] = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row, autocommit=True)
    if phase == "extract":
        from doc_ray.adapters.pdf_image_source_pymupdf import PyMuPDFImageSource
        from doc_ray.adapters.pdf_renderer_pymupdf import PyMuPDFRenderer

        _G["renderer"], _G["image_source"] = PyMuPDFRenderer(), PyMuPDFImageSource()
    else:
        from doc_ray.adapters.image_ocr_pymupdf import PyMuPDFImageOcr

        if os.environ.get("DOCRAY_VLM_BACKEND") == "vllm":
            from doc_ray.adapters.vlm_vllm import VLLMVisionLanguageModel

            _G["vlm"] = VLLMVisionLanguageModel()
        else:
            from doc_ray.adapters.vlm_ollama import OllamaVisionLanguageModel

            _G["vlm"] = OllamaVisionLanguageModel()
        _G["ocr"] = PyMuPDFImageOcr()
    signal.signal(signal.SIGALRM, _on_alarm)


def _process(doc_id: str) -> tuple[str, str, Any, float]:
    """Run one document through the current phase (in a pool worker)."""
    from doc_ray.application.visual_caption import caption_document_images
    from doc_ray.application.visual_extract import extract_document_visuals

    t0 = time.monotonic()
    signal.alarm(WATCHDOG_SECONDS)
    try:
        if _G["phase"] == "extract":
            r = extract_document_visuals(
                _G["conn"],
                _G["store"],
                doc_id,
                renderer=_G["renderer"],
                image_source=_G["image_source"],
            )
        else:
            r = caption_document_images(
                _G["conn"], _G["store"], doc_id, vlm=_G["vlm"], image_ocr=_G["ocr"]
            )
        signal.alarm(0)
        return (doc_id, "ok", r, time.monotonic() - t0)
    except _DocTimeout:
        signal.alarm(0)
        return (doc_id, "timeout", None, time.monotonic() - t0)
    except Exception as exc:  # noqa: BLE001 — isolate per-doc failures
        signal.alarm(0)
        return (doc_id, "error", f"{type(exc).__name__}: {exc}", time.monotonic() - t0)


def _eligible_ids(conn: psycopg.Connection[Any], phase: str) -> list[str]:
    if phase == "caption":
        rows = conn.execute(
            "SELECT DISTINCT d.id, d.page_count FROM documents d "
            "JOIN document_image di ON di.document_id = d.id "
            "ORDER BY d.page_count ASC NULLS LAST, d.id"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, page_count FROM documents "
            "WHERE (doc_type='pdf' OR mime_type='application/pdf') "
            "AND status IN ('extracted','failed') "
            "ORDER BY page_count ASC NULLS LAST, id"
        ).fetchall()
    return [str(r["id"]) for r in rows]


def _summary(phase: str, r: Any) -> str:
    if phase == "extract":
        return (
            f"images={r.get('images', 0)} (raster={r.get('embedded_rasters', 0)} "
            f"region={r.get('rendered_regions', 0)} page={r.get('full_page_renders', 0)})"
        )
    return (
        f"committed={r.get('committed', 0)} review={r.get('review', 0)} "
        f"rejected={r.get('rejected', 0)}"
    )


def main() -> int:
    phase = sys.argv[1] if len(sys.argv) > 1 else "extract"
    if phase not in ("extract", "caption"):
        print(f"usage: prism_backfill.py [extract|caption]  (got {phase!r})", file=sys.stderr)
        return 2
    workers = int(os.environ.get("FULL_PASS_WORKERS", "16" if phase == "extract" else "4"))

    os.makedirs(_WORK, exist_ok=True)
    done_path = os.path.join(_WORK, f"prism_backfill_{phase}.done")
    log_path = os.path.join(_WORK, f"prism_backfill_{phase}.log")
    done: set[str] = set()
    if os.path.exists(done_path):
        with open(done_path) as fh:
            done = {ln.strip() for ln in fh if ln.strip()}
    log = open(log_path, "a", buffering=1)  # noqa: SIM115 — streamed for the whole run
    done_fh = open(done_path, "a", buffering=1)  # noqa: SIM115 — checkpoint appended per doc

    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row, autocommit=True) as conn:
        ids = _eligible_ids(conn, phase)
    todo = [i for i in ids if i not in done]
    header = (
        f"\n=== phase={phase} start: {len(todo)} to do "
        f"({len(done)} done, {len(ids)} total) x{workers} workers ==="
    )
    log.write(header + "\n")
    print(header + f"  (progress -> {log_path})")
    started = time.monotonic()
    totals = {"images": 0, "committed": 0, "review": 0, "rejected": 0}
    ok = timeout = err = 0

    ctx = mp.get_context("fork")
    with ctx.Pool(workers, initializer=_init_worker, initargs=(phase,)) as pool:
        for n, (doc_id, status, r, dt) in enumerate(pool.imap_unordered(_process, todo), 1):
            if status == "ok":
                ok += 1
                done_fh.write(doc_id + "\n")
                if phase == "extract":
                    totals["images"] += int(r.get("images", 0))
                else:
                    for k in ("committed", "review", "rejected"):
                        totals[k] += int(r.get(k, 0))
                line = f"{doc_id[:8]} {_summary(phase, r)} {dt:.1f}s"
            elif status == "timeout":
                timeout += 1
                line = f"{doc_id[:8]} TIMEOUT >{WATCHDOG_SECONDS}s — skipped"
            else:
                err += 1
                line = f"{doc_id[:8]} ERROR {r}"
            elapsed = time.monotonic() - started
            rate = n / elapsed if elapsed else 0
            eta = (len(todo) - n) / rate / 60 if rate else 0
            log.write(
                f"[{n}/{len(todo)}] {line} | ok={ok} to={timeout} err={err} "
                f"| {rate * 60:.0f}/min eta {eta:.0f}m | cum {totals}\n"
            )
    footer = (
        f"=== phase={phase} DONE: ok={ok} timeout={timeout} err={err} {totals} "
        f"in {(time.monotonic() - started) / 60:.1f}m ==="
    )
    log.write(footer + "\n")
    print(footer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
