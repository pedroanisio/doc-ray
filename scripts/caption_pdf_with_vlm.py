#!/usr/bin/env python3
"""Operator CLI: run a local PDF's pages through the Tier-3 caption VLM.

A bench, not a pipeline. It answers "what does the caption model actually say
about this file?" for a PDF that is not (or not yet) in the corpus — the case a
scanned document raises, where the text layer is empty and the only reader left
is a vision model.

WHAT IT SHARES WITH THE REAL PIPELINE. Everything that decides the answer:

* the backend/model/decoding come from ``adapters.vlm_factory.make_vlm``, so
  whatever ``DOCRAY_VLM_*`` the caption worker reads, this reads too (Ollama
  ``qwen3-vl:32b-instruct`` by default; ``DOCRAY_VLM_BACKEND=vllm`` with
  ``DOCRAY_VLM_ENDPOINT``/``DOCRAY_VLM_API_KEY`` for a hosted endpoint);
* pages are rendered by the same ``PyMuPDFRenderer`` at the same default DPI;
* the caption is produced from the image ALONE, then scored and gated by
  ``application.visual_caption.gate_caption`` at ``AUTO_COMMIT_THRESHOLD`` —
  the same committed/review/rejected verdict Tier-3 would reach;
* grounding evidence is a deterministic OCR of the rendered page, which the
  model never sees, exactly as Tier-3 grounds a crop it cannot game.

WHAT IT DELIBERATELY DOES NOT DO. It touches no database and no object store, so
it cannot create, promote, or caption a corpus document. Ingest a file if you
want it in the corpus; use this to find out whether that is worth doing.

WHAT DIFFERS FROM TIER-3. Tier-3 captions *cropped image regions* discovered by
Tier-1; this captions whole pages, because for a scan the page IS the figure and
no region pass has run. Page captions are therefore coarser than the ones the
pipeline would store.

Usage (needs the VLM backend reachable — a served Ollama or a hosted endpoint):

    python scripts/caption_pdf_with_vlm.py _work/BE-0401298-A.pdf
    python scripts/caption_pdf_with_vlm.py _work/BE-0401298-A.pdf --pages 1-4
    python scripts/caption_pdf_with_vlm.py file.pdf --dpi 200 --out out.json

Writes a JSON report (``--out``, default ``<pdf>.vlm.json``) and prints a
per-page summary. Exit 0 when every requested page was captioned, 1 on a
backend/render failure, 2 on bad arguments.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from doc_ray.adapters.image_ocr_pymupdf import PyMuPDFImageOcr
from doc_ray.adapters.pdf_renderer_pymupdf import PyMuPDFRenderer
from doc_ray.adapters.vlm_factory import make_vlm
from doc_ray.application.resolution import AUTO_COMMIT_THRESHOLD
from doc_ray.application.visual_caption import gate_caption
from doc_ray.ports.pdf_renderer import DEFAULT_RENDER_DPI
from doc_ray.ports.vlm import VisionLanguageModel


def parse_pages(spec: str | None, page_count: int) -> list[int]:
    """Return the 1-based pages named by *spec* (``"3"``, ``"2-5"``, ``"1,4,7-9"``).

    ``None`` means every page. Out-of-range pages are rejected rather than
    silently clamped: a typo that quietly captions the wrong pages costs real
    model time.
    """
    if spec is None:
        return list(range(1, page_count + 1))
    pages: list[int] = []
    for part in spec.split(","):
        chunk = part.strip()
        if not chunk:
            continue
        if "-" in chunk:
            first, _, last = chunk.partition("-")
            start, end = int(first), int(last)
        else:
            start = end = int(chunk)
        if start < 1 or end < start or end > page_count:
            raise ValueError(f"page range {chunk!r} is outside 1-{page_count}")
        pages.extend(range(start, end + 1))
    if not pages:
        raise ValueError("no pages selected")
    return sorted(dict.fromkeys(pages))


def describe_backend(vlm: VisionLanguageModel) -> dict[str, Any]:
    """Return the resolved backend identity, for the report header and the log.

    Printed before any request so the run is attributable: "which model said
    this" is the first question asked of any caption, and the answer must not
    depend on remembering what the environment held at the time.
    """
    endpoint = getattr(vlm, "_endpoint", None) or getattr(vlm, "_host", None)
    return {
        "model": vlm.model_id,
        "model_version": vlm.model_version,
        "endpoint": str(endpoint) if endpoint else None,
        "grounding_threshold": AUTO_COMMIT_THRESHOLD,
    }


def caption_page(
    vlm: VisionLanguageModel,
    ocr: PyMuPDFImageOcr | None,
    page_png: bytes,
    page_number: int,
) -> dict[str, Any]:
    """Caption one rendered page and gate it exactly as Tier-3 would.

    ⚠ ARCHITECTURAL CONTRACT (PALS's LAW) — LLM OUTPUT IS UNVERIFIED BY DEFAULT

    LLMs statistically produce errors: omissions, hallucinations,
    partial completions, schema violations, and silent failures.
    These are properties of the model class, not exceptional conditions.

    Any caller of this function that skips output validation is
    introducing an architectural omission — not a code bug downstream.

    Verification is mandatory. Treat all LLM output as untrusted input.

    The verification here is the returned ``status``/``grounding`` pair, computed
    by the pipeline's own deterministic scorer against page OCR the model never
    saw. A ``review`` status means the caption is NOT corroborated — it is not a
    softer form of success, and no consumer of this report may treat it as one.
    """
    ocr_text = ocr.ocr_image(page_png) if ocr is not None else ""
    started = time.monotonic()
    output = vlm.caption_image(page_png, "")
    elapsed = time.monotonic() - started
    decision = gate_caption(output, ocr_text, threshold=AUTO_COMMIT_THRESHOLD)
    return {
        "page": page_number,
        "caption": output.caption,
        "status": decision.status,
        "grounding_score": round(decision.grounding.score, 4),
        "grounded_terms": sorted(decision.grounding.matched_terms),
        "insufficient_specificity": decision.grounding.insufficient_specificity,
        "ocr_chars": len(ocr_text),
        # Only the hosted (vllm) adapter reports token usage; Ollama returns a
        # bare caption, so this stays None there rather than inventing a number.
        "usage": output.analysis.get("usage"),
        "caption_chars": len(output.caption),
        "seconds": round(elapsed, 2),
    }


def run(
    pdf_path: Path,
    *,
    pages_spec: str | None,
    dpi: int,
    use_ocr: bool,
) -> dict[str, Any]:
    """Render and caption the requested pages; return the full report payload."""
    pdf_bytes = pdf_path.read_bytes()
    renderer = PyMuPDFRenderer()
    page_count = renderer.page_count(pdf_bytes)
    pages = parse_pages(pages_spec, page_count)
    vlm = make_vlm()
    ocr = PyMuPDFImageOcr() if use_ocr else None
    backend = describe_backend(vlm)
    print(
        f"model={backend['model']} ({backend['model_version']}) "
        f"endpoint={backend['endpoint']} dpi={dpi} "
        f"pages={len(pages)}/{page_count} grounding_ocr={'on' if use_ocr else 'off'}",
        flush=True,
    )

    results: list[dict[str, Any]] = []
    for position, page_number in enumerate(pages, start=1):
        page_png = renderer.render_page_png(pdf_bytes, page_number, dpi=dpi)
        record = caption_page(vlm, ocr, page_png, page_number)
        results.append(record)
        head = " ".join(record["caption"].split())[:96]
        print(
            f"[{position}/{len(pages)}] p{page_number} "
            f"{record['status']:<9} g={record['grounding_score']:<6} "
            f"{record['seconds']}s  {head}",
            flush=True,
        )
    return {
        "source": str(pdf_path),
        "page_count": page_count,
        "dpi": dpi,
        "backend": backend,
        "pages": results,
        "untrusted": (
            "VLM output. 'review' means the caption is not corroborated by page "
            "OCR and must not be used as fact without a human read."
        ),
    }


def _build_parser() -> argparse.ArgumentParser:
    """Return the CLI parser."""
    parser = argparse.ArgumentParser(
        description="Caption a local PDF's pages with the Tier-3 caption VLM.",
    )
    parser.add_argument("pdf", type=Path, help="Path to the PDF to caption.")
    parser.add_argument(
        "--pages",
        default=None,
        help="1-based pages to caption, e.g. '1', '2-5', '1,4,7-9' (default: all).",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=DEFAULT_RENDER_DPI,
        help=f"Render resolution (default {DEFAULT_RENDER_DPI}, the pipeline's).",
    )
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help="Skip page OCR. Every caption then scores ungrounded — diagnostic only.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="JSON report path (default: <pdf>.vlm.json beside the PDF).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI.

    Args:
        argv: Argument vector; defaults to ``sys.argv`` when ``None``.

    Returns:
        0 on success, 1 on a render/backend failure, 2 on bad arguments.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.pdf.is_file():
        print(f"no such file: {args.pdf}", file=sys.stderr)
        return 2
    out_path = args.out or args.pdf.with_suffix(args.pdf.suffix + ".vlm.json")

    try:
        report = run(
            args.pdf,
            pages_spec=args.pages,
            dpi=args.dpi,
            use_ocr=not args.no_ocr,
        )
    except ValueError as exc:
        print(f"bad arguments: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — a bench must report, not traceback
        print(f"caption run failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "Is the VLM backend reachable? Ollama needs the `ollama` service up "
            "and the model pulled; a hosted endpoint needs DOCRAY_VLM_BACKEND=vllm "
            "with DOCRAY_VLM_ENDPOINT and DOCRAY_VLM_API_KEY.",
            file=sys.stderr,
        )
        return 1

    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    statuses: dict[str, int] = {}
    for record in report["pages"]:
        statuses[record["status"]] = statuses.get(record["status"], 0) + 1
    tally = ", ".join(f"{count} {status}" for status, count in sorted(statuses.items()))
    print(f"\n{len(report['pages'])} page(s) captioned: {tally}")
    print(f"report: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
