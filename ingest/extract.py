"""Text/metadata extraction per document type. Currently: PDF.

PDFs are extracted with PyMuPDF (``extract_pdf_pymupdf``), which preserves
formatting by reconstructing structure from glyph geometry + font sizes into
Markdown (see ``ingest.pdf_layout``). pypdf remains as a flat-text fallback if
PyMuPDF is unavailable or errors on a file.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any, Optional

import pypdf
from pypdf import PdfReader

from . import pdf_layout


@dataclass
class Extracted:
    doc_type: str
    title: str
    author: Optional[str]
    page_count: Optional[int]
    full_text: str
    content_type: str
    extractor: str                       # tool that produced the text
    extractor_version: str               # its version — recorded in provenance
    metadata: dict[str, Any] = field(default_factory=dict)


# --- PyMuPDF: layout-aware, structured Markdown ----------------------------

def _spans_of_line(line: dict) -> list[dict]:
    spans = []
    for sp in line.get("spans", []):
        x0, _, x1, _ = sp["bbox"]
        spans.append({
            "text": sp["text"], "x0": x0, "x1": x1,
            "size": sp["size"], "bold": bool(sp.get("flags", 0) & 16),
        })
    return spans


def extract_pdf_pymupdf(data: bytes, fallback_title: str) -> Extracted:
    import fitz

    doc = fitz.open(stream=data, filetype="pdf")
    blocks: list[dict] = []
    for pageno, page in enumerate(doc):
        page_h = page.rect.height
        for b in page.get_text("dict").get("blocks", []):
            if b.get("type", 0) != 0:            # 0 = text block; skip images
                continue
            lines = []
            for ln in b.get("lines", []):
                spans = _spans_of_line(ln)
                if not spans:
                    continue
                boxes = [sp["bbox"] for sp in ln["spans"]]
                lines.append({
                    "spans": spans,
                    "y0": min(y[1] for y in boxes), "y1": max(y[3] for y in boxes),
                    "page": pageno, "page_h": page_h,
                })
            if lines:
                blocks.append({"lines": lines})

    full_text = pdf_layout.to_markdown(blocks)
    meta = doc.metadata or {}
    return Extracted(
        doc_type="pdf",
        title=(meta.get("title") or fallback_title).strip() or fallback_title,
        author=meta.get("author") or None,
        page_count=doc.page_count,
        full_text=full_text,
        content_type="application/pdf",
        extractor="pymupdf",
        extractor_version=str(getattr(fitz, "version", ("?",))[0]),
        metadata={"pdf_producer": meta.get("producer"),
                  "pdf_creator": meta.get("creator"),
                  "pdf_subject": meta.get("subject")},
    )


# --- pypdf: flat-text fallback --------------------------------------------

def extract_pdf_pypdf(data: bytes, fallback_title: str) -> Extracted:
    reader = PdfReader(io.BytesIO(data))
    meta = reader.metadata or {}
    full_text = pdf_layout.sanitize_text(
        "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    )
    return Extracted(
        doc_type="pdf",
        title=(meta.title or fallback_title).strip() or fallback_title,
        author=(meta.author or None),
        page_count=len(reader.pages),
        full_text=full_text,
        content_type="application/pdf",
        extractor="pypdf",
        extractor_version=pypdf.__version__,
        metadata={"pdf_producer": meta.producer,
                  "pdf_creator": meta.creator,
                  "pdf_subject": meta.subject},
    )


def extract_pdf(data: bytes, fallback_title: str) -> Extracted:
    """Prefer the structured PyMuPDF path; fall back to flat pypdf on any error."""
    try:
        import fitz  # noqa: F401
        return extract_pdf_pymupdf(data, fallback_title)
    except Exception:
        return extract_pdf_pypdf(data, fallback_title)


# Dispatch table keyed by lowercase file extension.
_EXTRACTORS = {
    ".pdf": extract_pdf,
}


def extract(data: bytes, filename: str, extension: str) -> Extracted:
    fn = _EXTRACTORS.get(extension.lower())
    if fn is None:
        raise ValueError(f"unsupported document type: {extension!r}")
    return fn(data, fallback_title=filename)
