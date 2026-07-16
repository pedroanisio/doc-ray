#!/usr/bin/env python3
"""Image-level Tier-3 caption backfill via vLLM (saturates the GPU).

Doc-granular workers starve vLLM's continuous batching (a 6000-image document
captions one request at a time). This driver fans out at the IMAGE level: a
thread pool keeps ~N caption requests in flight so vLLM batches them, while OCR
(optional, CPU) and the gated DB write happen per image. Each caption is grounded
against page text (+ crop OCR when enabled) then gated — identical semantics to
``visual_caption.caption_document_images``, just parallelized and idempotent per
image (delete+insert), so re-runs are safe.

OCR-of-crop grounding is off by default: PyMuPDF OCR is CPU/GIL-bound and caps
throughput; captions are stored regardless of gate outcome, so re-grounding can
run separately. Set CAPTION_OCR=1 to include it.

Env: DOCRAY_VLM_ENDPOINT, DOCRAY_VLM_MODEL, DOCRAY_VLM_NUM_PREDICT,
     CAPTION_CONCURRENCY (default 64), CAPTION_OCR (0/1), CAPTION_LIMIT (0=all),
     PRISM_WORK_DIR (log dir, default system temp).

Disclaimer: see ../DISCLAIMER.md. No statement or premise not backed by a real
logical definition or verifiable reference should be taken for granted.
"""

# ruff: noqa: E501 — long f-string progress/log lines.
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from doc_ray.adapters.image_ocr_pymupdf import PyMuPDFImageOcr
from doc_ray.adapters.object_store import ObjectStore
from doc_ray.adapters.vlm_vllm import VLLMVisionLanguageModel
from doc_ray.application.resolution import AUTO_COMMIT_THRESHOLD
from doc_ray.application.visual_caption import STATUS_REVIEW, _grounded_against, gate_caption
from doc_ray.config import Config
from doc_ray.domain.ulid import new_ulid_str

_WORK = os.environ.get("PRISM_WORK_DIR") or os.path.join(tempfile.gettempdir(), "prism-work")
CONCURRENCY = int(os.environ.get("CAPTION_CONCURRENCY", "64"))
OCR_ON = os.environ.get("CAPTION_OCR", "0") == "1"

_store = ObjectStore(Config.from_env())
_vlm = VLLMVisionLanguageModel()
_ocr = PyMuPDFImageOcr()
_model = _vlm.model_id

_evidence_cache: dict[str, dict[int, str]] = {}
_evidence_lock = threading.Lock()
_counts: dict[str, int] = {"committed": 0, "review": 0, "rejected": 0, "error": 0}
_counts_lock = threading.Lock()


def _page_evidence(pool: ConnectionPool, doc_id: str) -> dict[int, str]:
    """Per-document page->text cache; queried once per document."""
    cached = _evidence_cache.get(doc_id)
    if cached is not None:
        return cached
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT sp.page_number AS page, string_agg(s.text, ' ' ORDER BY s.ordinal) AS txt "
            "FROM sentences s JOIN spans sp ON sp.id = s.span_id "
            "WHERE s.document_id = %s AND sp.page_number IS NOT NULL "
            "GROUP BY sp.page_number",
            (doc_id,),
        ).fetchall()
    ev = {int(r["page"]): (r["txt"] or "") for r in rows}
    with _evidence_lock:
        _evidence_cache[doc_id] = ev
    return ev


def _caption_one(pool: ConnectionPool, img: dict[str, Any]) -> str:
    doc_id, image_id = str(img["document_id"]), str(img["id"])
    crop = _store.get(str(img["object_key"]))
    page_text = _page_evidence(pool, doc_id).get(int(img["page_number"]), "")
    ocr_text = _ocr.ocr_image(crop) if OCR_ON else ""
    evidence = f"{page_text} {ocr_text}".strip()
    out = _vlm.caption_image(crop, "")  # image-only (anti-gaming)
    decision = gate_caption(out, evidence, threshold=AUTO_COMMIT_THRESHOLD)
    grounded = _grounded_against(decision)
    with pool.connection() as conn, conn.transaction():
        conn.execute(
            "DELETE FROM visual_caption WHERE document_image_id=%s AND model=%s",
            (image_id, _model),
        )
        conn.execute("DELETE FROM visual_review WHERE document_image_id=%s", (image_id,))
        conn.execute(
            "INSERT INTO visual_caption (id, document_image_id, document_id, caption, analysis, "
            "model, model_version, grounding_score, grounded_against, status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                new_ulid_str(),
                image_id,
                doc_id,
                out.caption,
                json.dumps(out.analysis),
                _model,
                _vlm.model_version,
                decision.grounding.score,
                json.dumps(grounded),
                decision.status,
            ),
        )
        if decision.status == STATUS_REVIEW:
            conn.execute(
                "INSERT INTO visual_review (id, document_image_id, document_id, proposed, "
                "grounding_score, grounding_evidence, status) VALUES (%s,%s,%s,%s,%s,%s,'pending')",
                (
                    new_ulid_str(),
                    image_id,
                    doc_id,
                    json.dumps({"caption": out.caption, "analysis": out.analysis}),
                    decision.grounding.score,
                    json.dumps(grounded),
                ),
            )
    return decision.status


def main() -> int:
    os.makedirs(_WORK, exist_ok=True)
    log = open(os.path.join(_WORK, "prism_caption.log"), "a", buffering=1)  # noqa: SIM115 — streamed for the whole run
    url = os.environ["DATABASE_URL"]
    with psycopg.connect(url, row_factory=dict_row, autocommit=True) as conn:
        done = {
            str(r["document_image_id"])
            for r in conn.execute(
                "SELECT document_image_id FROM visual_caption WHERE model=%s", (_model,)
            ).fetchall()
        }
        images = conn.execute(
            "SELECT id, document_id, object_key, page_number FROM document_image ORDER BY document_id"
        ).fetchall()
    todo = [im for im in images if str(im["id"]) not in done]
    limit = int(os.environ.get("CAPTION_LIMIT", "0"))
    if limit:
        todo = todo[:limit]
    header = (
        f"\n=== caption start: {len(todo)} images "
        f"({len(done)} done, {len(images)} total) model={_model} x{CONCURRENCY} ocr={OCR_ON} ==="
    )
    log.write(header + "\n")
    print(header + f"  (progress -> {_WORK}/prism_caption.log)")
    started = time.monotonic()
    pool = ConnectionPool(
        url, min_size=8, max_size=32, kwargs={"autocommit": True, "row_factory": dict_row}
    )
    n = 0
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futs = [ex.submit(_caption_one, pool, im) for im in todo]
        for fut in as_completed(futs):
            n += 1
            try:
                status = fut.result()
                with _counts_lock:
                    _counts[status] = _counts.get(status, 0) + 1
            except Exception as exc:  # noqa: BLE001 — isolate per-image failures
                with _counts_lock:
                    _counts["error"] += 1
                if _counts["error"] <= 20:
                    log.write(f"  ERR {type(exc).__name__}: {exc}\n")
            if n % 200 == 0 or n == len(todo):
                el = time.monotonic() - started
                rate = n / el if el else 0
                eta = (len(todo) - n) / rate / 60 if rate else 0
                log.write(f"[{n}/{len(todo)}] {rate:.1f} img/s eta {eta:.0f}m | {dict(_counts)}\n")
    pool.close()
    footer = f"=== caption DONE: {dict(_counts)} in {(time.monotonic() - started) / 60:.1f}m ==="
    log.write(footer + "\n")
    print(footer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
