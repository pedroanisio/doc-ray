#!/usr/bin/env python3
"""Benchmark caption VLMs on real corpus images and rank them by ROI.

For a random-but-reproducible sample of document images this script replays the
EXACT production Tier-3 path — image-only caption, page-prose + crop-OCR
grounding evidence, ``gate_caption`` verdict — for every candidate model, then
reports the metric that actually matters: committed captions per dollar. The
grounding gate is the production quality bar (PALS: model output is untrusted
until grounded), so a cheaper model that commits nearly as often IS the better
ROI, no human judging required.

Cost comes from the endpoint's own token usage (``VLMOutput.analysis['usage']``)
priced with the table below; models without usage (Ollama) report $0 marginal.

Usage::

    set -a; source .env; set +a
    python scripts/vlm_roi_bench.py --sample 12 \
      --model ollama=qwen3-vl:32b-instruct \
      --model vllm=kimi-k2.5 --model vllm=nemotron-nano-12b-v2-vl \
      --remaining-images 62685

Env: DATABASE_URL + S3_* (sink), DOCRAY_VLM_ENDPOINT + DOCRAY_VLM_API_KEY for
``vllm=`` models, DOCRAY_OLLAMA_HOST for ``ollama=`` models.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any

from doc_ray.adapters import persistence as db
from doc_ray.adapters.image_ocr_pymupdf import PyMuPDFImageOcr
from doc_ray.adapters.object_store import ObjectStore
from doc_ray.adapters.vlm_ollama import OllamaVisionLanguageModel
from doc_ray.adapters.vlm_vllm import VLLMVisionLanguageModel
from doc_ray.application.visual_caption import STATUS_COMMITTED, STATUS_REVIEW, gate_caption
from doc_ray.config import Config
from doc_ray.ports.vlm import VisionLanguageModel

#: USD per 1M tokens (input, output) on DigitalOcean serverless inference.
#: Source: https://docs.digitalocean.com/products/inference/details/pricing/
#: (prices change; re-check before relying on them). Models absent here report
#: token counts but no dollars.
DO_PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "kimi-k3": (3.00, 15.00),
    "kimi-k2.6": (0.76, 3.20),
    "kimi-k2.5": (0.375, 2.025),
    "nemotron-nano-12b-v2-vl": (0.20, 0.60),
}

_SAMPLE_SQL = """
SELECT DISTINCT ON (di.document_id) di.id, di.document_id, di.object_key, di.page_number
FROM document_image di
WHERE EXISTS (SELECT 1 FROM sentences s JOIN spans sp ON sp.id = s.span_id
              WHERE s.document_id = di.document_id AND sp.page_number = di.page_number)
ORDER BY di.document_id, md5(di.id::text || %(seed)s)
"""

_EVIDENCE_SQL = """
SELECT s.text FROM sentences s JOIN spans sp ON sp.id = s.span_id
WHERE s.document_id = %(doc)s AND sp.page_number = %(page)s ORDER BY s.ordinal
"""


def _make_vlm(backend: str, model: str) -> VisionLanguageModel:
    if backend == "ollama":
        return OllamaVisionLanguageModel(model=model)
    if backend == "vllm":
        return VLLMVisionLanguageModel(model=model)
    raise SystemExit(f"unknown backend {backend!r} (expected ollama= or vllm=)")


def _sample_images(conn: Any, sample: int, seed: str) -> list[dict[str, Any]]:
    """One image per document (deterministic per seed), capped at *sample*."""
    rows = conn.execute(_SAMPLE_SQL, {"seed": seed}).fetchall()
    ranked = sorted(rows, key=lambda r: str(r["id"]))
    return ranked[:sample]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sample", type=int, default=12, help="images to bench (default 12)")
    parser.add_argument("--seed", default="roi-1", help="sampling seed (default roi-1)")
    parser.add_argument(
        "--model",
        action="append",
        required=True,
        metavar="BACKEND=NAME",
        help="candidate, repeatable: ollama=qwen3-vl:32b-instruct or vllm=kimi-k2.5",
    )
    parser.add_argument(
        "--remaining-images",
        type=int,
        default=0,
        help="project corpus cost/wall-clock over this many images",
    )
    parser.add_argument(
        "--host-usd-per-hour",
        type=float,
        default=0.0,
        help="this machine's hourly price; prices local (tokenless) models by GPU-seconds"
        " — local inference on a paid host is never $0",
    )
    args = parser.parse_args()

    candidates = [tuple(spec.split("=", 1)) for spec in args.model]
    if any(len(c) != 2 for c in candidates):
        parser.error("--model must be BACKEND=NAME")

    cfg = Config.from_env()
    store = ObjectStore(cfg)
    ocr = PyMuPDFImageOcr()
    with db.connect(cfg) as conn:
        images = _sample_images(conn, args.sample, args.seed)
        if not images:
            print("no images with page evidence found", file=sys.stderr)
            return 2
        cases = []
        for image in images:
            page_rows = conn.execute(
                _EVIDENCE_SQL, {"doc": image["document_id"], "page": image["page_number"]}
            ).fetchall()
            crop = store.get(str(image["object_key"]))
            evidence = f"{' '.join(r['text'] for r in page_rows)} {ocr.ocr_image(crop)}".strip()
            cases.append((str(image["id"]), crop, evidence))
    print(f"benching {len(candidates)} model(s) on {len(cases)} image(s), seed={args.seed}\n")

    for backend, model in candidates:
        vlm = _make_vlm(backend, model)
        committed = review = errors = 0
        latencies: list[float] = []
        tok_in = tok_out = 0
        scores: list[float] = []
        for image_id, crop, evidence in cases:
            t0 = time.monotonic()
            try:
                out = vlm.caption_image(crop, "")
            except Exception as exc:  # noqa: BLE001 — a bench must survive one bad call
                errors += 1
                print(f"  [{model}] {image_id}: ERROR {type(exc).__name__}: {exc}")
                continue
            latencies.append(time.monotonic() - t0)
            usage = out.analysis.get("usage") or {}
            tok_in += int(usage.get("prompt_tokens") or 0)
            tok_out += int(usage.get("completion_tokens") or 0)
            decision = gate_caption(out, evidence)
            scores.append(decision.grounding.score)
            committed += decision.status == STATUS_COMMITTED
            review += decision.status == STATUS_REVIEW
        n = len(latencies)
        print(f"model {backend}={model}")
        if not n:
            print(f"  all {errors} call(s) failed\n")
            continue
        prices = DO_PRICES_PER_MTOK.get(model)
        if prices:
            per_img: float | None = (tok_in / 1e6 * prices[0] + tok_out / 1e6 * prices[1]) / n
        elif args.host_usd_per_hour > 0:
            # Tokenless local model: price the GPU-seconds it occupies this host.
            per_img = (sum(latencies) / n) * args.host_usd_per_hour / 3600
        else:
            per_img = None
        print(
            f"  commit {committed}/{n}  review {review}/{n}  errors {errors}"
            f"  mean-grounding {sum(scores) / len(scores):.3f}"
        )
        print(
            f"  latency mean {sum(latencies) / n:.1f}s"
            f"  tokens/img in {tok_in / n:.0f} out {tok_out / n:.0f}"
            + (f"  cost/img ${per_img:.5f}" if per_img is not None else "  cost/img n/a")
        )
        if args.remaining_images and per_img is not None:
            total = per_img * args.remaining_images
            if committed:
                roi = f"${total / (committed / n):,.0f} per fully-committed corpus"
            else:
                roi = "ROI undefined: 0 committed — spend buys review-queue items only"
            print(f"  projected: ${total:,.0f} for {args.remaining_images:,} images  ({roi})")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
