"""PDF extraction: layout-aware PyMuPDF, flat pypdf fallback, opt-in ML math backend.

Split out of :mod:`doc_ray.adapters.extraction` (D-015). PDFs are extracted
with PyMuPDF (:func:`extract_pdf_pymupdf`), which preserves formatting by
reconstructing structure from glyph geometry + font sizes into Markdown (see
``doc_ray.domain.pdf_layout``); pypdf remains as a flat-text fallback if
PyMuPDF is unavailable or errors on a file.
"""

from __future__ import annotations

import io
import json
import os
import re as _re
import shlex
import subprocess
from collections import Counter
from collections.abc import Iterator, Mapping
from typing import Any, Protocol

import pypdf
from pypdf import PdfReader

from doc_ray.adapters.extraction_common import Extracted, env_float, env_int
from doc_ray.domain import pdf_layout
from doc_ray.domain.progress import report_progress


class _PyMuPDFRect(Protocol):
    width: float
    height: float


class _PyMuPDFPage(Protocol):
    rect: _PyMuPDFRect

    def get_textpage_ocr(self, *, language: str) -> object:
        """Return a PyMuPDF OCR text page."""
        ...

    def get_text(self, option: str, **kwargs: object) -> Mapping[str, Any]:
        """Return PyMuPDF text extraction output."""
        ...


class _PyMuPDFDocument(Protocol):
    def __iter__(self) -> Iterator[_PyMuPDFPage]:
        """Iterate over PyMuPDF pages."""
        ...


def pdf_ocr_enabled_from_env(env: Mapping[str, str] | None = None) -> bool:
    """Return whether image-only PDF OCR is explicitly enabled."""
    source = os.environ if env is None else env
    return source.get("DOCRAY_PDF_OCR", "").strip().lower() in {"1", "true", "yes", "on"}


def pdf_math_backend_from_env(env: Mapping[str, str] | None = None) -> str | None:
    """Return the opt-in PDF math backend mode, if configured."""
    source = os.environ if env is None else env
    raw = source.get("DOCRAY_PDF_MATH_BACKEND", "").strip().lower()
    return raw or None


def _latex_equation_present(text: str) -> bool:
    return bool(
        _re.search(
            r"(\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|\$\$[\s\S]+?\$\$|\\begin\{equation\})",
            text,
        )
    )


def _verified_math_payload(payload: object, fallback_title: str) -> Extracted:
    if not isinstance(payload, dict):
        raise ValueError("Math extraction backend must emit a JSON object.")
    full_text = str(payload.get("full_text") or "")
    if not full_text.strip():
        raise ValueError("Math extraction backend emitted empty text.")
    if not _latex_equation_present(full_text):
        raise ValueError("Math extraction backend output did not contain verified LaTeX.")
    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError("Math extraction backend metadata must be a JSON object.")
    extractor = str(payload.get("extractor") or "external-math")
    extractor_version = str(payload.get("extractor_version") or "unknown")
    return Extracted(
        doc_type="pdf",
        title=str(payload.get("title") or fallback_title).strip() or fallback_title,
        author=payload.get("author") if isinstance(payload.get("author"), str) else None,
        page_count=payload.get("page_count")
        if isinstance(payload.get("page_count"), int)
        else None,
        full_text=full_text,
        content_type="application/pdf",
        extractor=extractor,
        extractor_version=extractor_version,
        metadata={
            **metadata,
            "math_backend": "external",
            "math_backend_verified": True,
            "math_backend_contract": "json-full_text-with-latex",
        },
    )


def extract_pdf_math_backend(data: bytes, fallback_title: str) -> Extracted:
    """Extract a PDF with an operator-configured ML math backend.

    The external command receives PDF bytes on stdin and must emit JSON on
    stdout with at least ``full_text``, ``extractor``, and
    ``extractor_version``. Output is rejected unless it contains explicit LaTeX
    equation markup; unverified ML output must never be persisted.
    """
    command = os.environ.get("DOCRAY_PDF_MATH_BACKEND_CMD", "").strip()
    if not command:
        raise ValueError("DOCRAY_PDF_MATH_BACKEND_CMD is required for the external backend.")
    result = subprocess.run(
        shlex.split(command),
        input=data,
        capture_output=True,
        check=True,
        timeout=env_int(os.environ, "DOCRAY_PDF_MATH_BACKEND_TIMEOUT", 120),
    )
    stdout = result.stdout.decode("utf-8") if isinstance(result.stdout, bytes) else result.stdout
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("Math extraction backend emitted invalid JSON.") from exc
    return _verified_math_payload(payload, fallback_title)


def pdf_heading_thresholds_from_env(
    env: Mapping[str, str] | None = None,
) -> pdf_layout.HeadingThresholds:
    """Load corpus-specific PDF heading thresholds from environment variables."""
    source = os.environ if env is None else env
    defaults = pdf_layout.DEFAULT_HEADING_THRESHOLDS
    return pdf_layout.HeadingThresholds(
        level1_ratio=env_float(source, "DOCRAY_PDF_HEADING_L1_RATIO", defaults.level1_ratio),
        level2_ratio=env_float(source, "DOCRAY_PDF_HEADING_L2_RATIO", defaults.level2_ratio),
        level3_ratio=env_float(source, "DOCRAY_PDF_HEADING_L3_RATIO", defaults.level3_ratio),
        bold_max_words=env_int(
            source,
            "DOCRAY_PDF_HEADING_BOLD_MAX_WORDS",
            defaults.bold_max_words,
        ),
    )


def pdf_ocr_language_from_env(env: Mapping[str, str] | None = None) -> str:
    """Return the Tesseract language code used by the PyMuPDF OCR backend."""
    source = os.environ if env is None else env
    return source.get("DOCRAY_PDF_OCR_LANGUAGE", "eng").strip() or "eng"


def _spans_of_line(line: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract simplified span dicts (text, geometry, size, bold) from a line."""
    spans = []
    for sp in line.get("spans", []):
        x0, _, x1, _ = sp["bbox"]
        spans.append(
            {
                "text": sp["text"],
                "x0": x0,
                "x1": x1,
                "size": sp["size"],
                "bold": bool(sp.get("flags", 0) & 16),
            }
        )
    return spans


def _blocks_from_pages(
    doc: _PyMuPDFDocument, *, ocr: bool = False, language: str = "eng"
) -> list[dict[str, Any]]:
    """Extract simplified text blocks from a PyMuPDF document."""
    blocks: list[dict[str, Any]] = []
    total_pages = int(getattr(doc, "page_count", 0) or 0)
    for pageno, page in enumerate(doc):
        verb = "OCRing" if ocr else "Extracting"
        report_progress(pageno, total_pages, f"{verb} PDF page {pageno + 1} of {total_pages}")
        page_w = page.rect.width
        page_h = page.rect.height
        textpage = page.get_textpage_ocr(language=language) if ocr else None
        page_dict = (
            page.get_text("dict", textpage=textpage)
            if textpage is not None
            else page.get_text("dict")
        )
        for b in page_dict.get("blocks", []):
            if b.get("type", 0) != 0:  # 0 = text block; skip images
                continue
            lines = []
            for ln in b.get("lines", []):
                spans = _spans_of_line(ln)
                if not spans:
                    continue
                boxes = [sp["bbox"] for sp in ln["spans"]]
                lines.append(
                    {
                        "spans": spans,
                        "y0": min(y[1] for y in boxes),
                        "y1": max(y[3] for y in boxes),
                        "page": pageno,
                        "page_w": page_w,
                        "page_h": page_h,
                    }
                )
            if lines:
                blocks.append({"lines": lines})
    return blocks


def _page_metrics_from_doc(
    doc: _PyMuPDFDocument, blocks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return one page-dimension record for every PDF page."""
    text_run_counts: Counter[int] = Counter()
    for block in blocks:
        for line in block["lines"]:
            text_run_counts[int(line["page"])] += 1
    metrics: list[dict[str, Any]] = []
    for pageno, page in enumerate(doc):
        text_run_count = int(text_run_counts[pageno])
        metrics.append(
            {
                "page": pageno,
                "page_width": float(page.rect.width),
                "page_height": float(page.rect.height),
                "has_text": text_run_count > 0,
                "text_run_count": text_run_count,
            }
        )
    return metrics


def extract_pdf_pymupdf(data: bytes, fallback_title: str) -> Extracted:
    """Extract a PDF into layout-aware Markdown using PyMuPDF.

    Args:
        data: Raw PDF bytes.
        fallback_title: Title to use when the PDF has no title metadata.

    Returns:
        The extracted document with structured Markdown text.
    """
    import fitz

    doc = fitz.open(stream=data, filetype="pdf")
    thresholds = pdf_heading_thresholds_from_env()
    blocks = _blocks_from_pages(doc)
    full_text = pdf_layout.to_markdown(blocks, thresholds=thresholds)
    ocr_enabled = pdf_ocr_enabled_from_env()
    ocr_applied = False
    if not full_text.strip() and ocr_enabled:
        language = pdf_ocr_language_from_env()
        blocks = _blocks_from_pages(doc, ocr=True, language=language)
        full_text = pdf_layout.to_markdown(blocks, thresholds=thresholds)
        ocr_applied = bool(full_text.strip())
    meta = doc.metadata or {}
    return Extracted(
        doc_type="pdf",
        title=(meta.get("title") or fallback_title).strip() or fallback_title,
        author=meta.get("author") or None,
        page_count=doc.page_count,
        full_text=full_text,
        content_type="application/pdf",
        extractor="pymupdf+ocr" if ocr_applied else "pymupdf",
        extractor_version=str(getattr(fitz, "version", ("?",))[0]),
        metadata={
            "pdf_producer": meta.get("producer"),
            "pdf_creator": meta.get("creator"),
            "pdf_subject": meta.get("subject"),
            "ocr_enabled": ocr_enabled,
            "ocr_applied": ocr_applied,
            "ocr_language": pdf_ocr_language_from_env() if ocr_enabled else None,
            "pdf_heading_thresholds": {
                "level1_ratio": thresholds.level1_ratio,
                "level2_ratio": thresholds.level2_ratio,
                "level3_ratio": thresholds.level3_ratio,
                "bold_max_words": thresholds.bold_max_words,
            },
        },
        page_runs=pdf_layout.page_runs_from_blocks(blocks),
        page_metrics=_page_metrics_from_doc(doc, blocks),
    )


def extract_pdf_pypdf(data: bytes, fallback_title: str) -> Extracted:
    """Extract a PDF into flat text using pypdf (the fallback path).

    Args:
        data: Raw PDF bytes.
        fallback_title: Title to use when the PDF has no title metadata.

    Returns:
        The extracted document with flat, sanitized text.
    """
    reader = PdfReader(io.BytesIO(data))
    meta = reader.metadata
    full_text = pdf_layout.sanitize_text(
        "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    )
    title = (meta.title if meta else None) or fallback_title
    return Extracted(
        doc_type="pdf",
        title=title.strip() or fallback_title,
        author=(meta.author if meta else None) or None,
        page_count=len(reader.pages),
        full_text=full_text,
        content_type="application/pdf",
        extractor="pypdf",
        extractor_version=pypdf.__version__,
        metadata={
            "pdf_producer": meta.producer if meta else None,
            "pdf_creator": meta.creator if meta else None,
            "pdf_subject": meta.subject if meta else None,
        },
    )


def extract_pdf(data: bytes, fallback_title: str) -> Extracted:
    """Prefer the structured PyMuPDF path; fall back to flat pypdf on any error.

    Args:
        data: Raw PDF bytes.
        fallback_title: Title to use when the PDF has no title metadata.

    Returns:
        The extracted document from whichever extractor succeeded.
    """
    math_backend = pdf_math_backend_from_env()
    if math_backend is not None:
        if math_backend != "external":
            raise ValueError("DOCRAY_PDF_MATH_BACKEND must be 'external' when set.")
        return extract_pdf_math_backend(data, fallback_title)
    try:
        import fitz  # noqa: F401

        return extract_pdf_pymupdf(data, fallback_title)
    except Exception:
        return extract_pdf_pypdf(data, fallback_title)
