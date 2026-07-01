"""Text/metadata extraction per document type. Currently: PDF.

PDFs are extracted with PyMuPDF (``extract_pdf_pymupdf``), which preserves
formatting by reconstructing structure from glyph geometry + font sizes into
Markdown (see ``ingest.pdf_layout``). pypdf remains as a flat-text fallback if
PyMuPDF is unavailable or errors on a file.
"""

from __future__ import annotations

import io
import posixpath
import re as _re
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser as _HTMLParser
from typing import Any
from xml.etree import ElementTree as ET

import pypdf
from pypdf import PdfReader

from doc_ray.domain import pdf_layout


@dataclass
class Extracted:
    """Normalized result of extracting text and metadata from one document."""

    doc_type: str
    title: str
    author: str | None
    page_count: int | None
    full_text: str
    content_type: str
    extractor: str  # tool that produced the text
    extractor_version: str  # its version — recorded in provenance
    metadata: dict[str, Any] = field(default_factory=dict)


# --- PyMuPDF: layout-aware, structured Markdown ----------------------------


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
    blocks: list[dict[str, Any]] = []
    for pageno, page in enumerate(doc):
        page_h = page.rect.height
        for b in page.get_text("dict").get("blocks", []):
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
                        "page_h": page_h,
                    }
                )
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
        metadata={
            "pdf_producer": meta.get("producer"),
            "pdf_creator": meta.get("creator"),
            "pdf_subject": meta.get("subject"),
        },
    )


# --- pypdf: flat-text fallback --------------------------------------------


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
    try:
        import fitz  # noqa: F401

        return extract_pdf_pymupdf(data, fallback_title)
    except Exception:
        return extract_pdf_pypdf(data, fallback_title)


# --- text / markup formats (byte-based, standard library) ------------------


def _text_extracted(
    text: str, fallback_title: str, *, doc_type: str, content_type: str, extractor: str
) -> Extracted:
    """Build an :class:`Extracted` for a plain-text/markup format."""
    return Extracted(
        doc_type=doc_type,
        title=fallback_title,
        author=None,
        page_count=None,
        full_text=(text or "").strip(),
        content_type=content_type,
        extractor=extractor,
        extractor_version="stdlib",
        metadata={},
    )


def extract_txt(data: bytes, fallback_title: str) -> Extracted:
    """Extract UTF-8 plain text.

    Args:
        data: Raw text bytes.
        fallback_title: Title for the extracted document.

    Returns:
        The extracted plain-text document.
    """
    return _text_extracted(
        data.decode("utf-8", "replace"),
        fallback_title,
        doc_type="text",
        content_type="text/plain",
        extractor="utf8",
    )


class _HTMLText(_HTMLParser):
    """Collect visible text from HTML/XHTML, skipping script/style, breaking blocks."""

    _SKIP = {"script", "style", "head", "title"}
    _BREAK = {"p", "br", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}

    def __init__(self) -> None:
        """Initialize the parser with charref conversion enabled."""
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Track skip depth for hidden tags and emit breaks for block tags."""
        if tag in self._SKIP:
            self._skip += 1
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        """Unwind skip depth and emit a trailing break for block tags."""
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        """Collect character data when not inside a skipped element."""
        if self._skip == 0:
            self.parts.append(data)


def _html_to_text(html: str) -> str:
    """Return the visible text of an HTML/XHTML string."""
    p = _HTMLText()
    p.feed(html)
    return "".join(p.parts)


def extract_html(data: bytes, fallback_title: str) -> Extracted:
    """Extract visible text from an HTML document.

    Args:
        data: Raw HTML bytes.
        fallback_title: Title for the extracted document.

    Returns:
        The extracted HTML document.
    """
    return _text_extracted(
        _html_to_text(data.decode("utf-8", "replace")),
        fallback_title,
        doc_type="html",
        content_type="text/html",
        extractor="html.parser",
    )


def _epub_spine(zf: zipfile.ZipFile) -> list[str]:
    """Return content-document paths in reading order (OPF spine), else all (x)html."""
    try:
        container = zf.read("META-INF/container.xml").decode("utf-8", "replace")
        match = _re.search(r'full-path="([^"]+)"', container)
        assert match is not None  # container.xml always names the OPF
        opf_path = match.group(1)
        opf = ET.fromstring(zf.read(opf_path))
        base = posixpath.dirname(opf_path)
        ns = {"o": "http://www.idpf.org/2007/opf"}
        manifest = {i.get("id"): i.get("href") for i in opf.iterfind(".//o:manifest/o:item", ns)}
        order = [
            posixpath.normpath(posixpath.join(base, href))
            for ref in opf.iterfind(".//o:spine/o:itemref", ns)
            if (href := manifest.get(ref.get("idref")))
        ]
        if order:
            return order
    except Exception:
        pass
    return sorted(n for n in zf.namelist() if n.lower().endswith((".xhtml", ".html", ".htm")))


def extract_epub(data: bytes, fallback_title: str) -> Extracted:
    """Extract text from an EPUB by concatenating its spine documents.

    Args:
        data: Raw EPUB (ZIP) bytes.
        fallback_title: Title for the extracted document.

    Returns:
        The extracted EPUB document.
    """
    parts: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in _epub_spine(zf):
            try:
                parts.append(_html_to_text(zf.read(name).decode("utf-8", "replace")))
            except KeyError:
                continue
    return _text_extracted(
        "\n".join(parts),
        fallback_title,
        doc_type="epub",
        content_type="application/epub+zip",
        extractor="stdlib-epub",
    )


def extract_docx(data: bytes, fallback_title: str) -> Extracted:
    """Extract text from a DOCX using python-docx.

    Args:
        data: Raw DOCX bytes.
        fallback_title: Title for the extracted document.

    Returns:
        The extracted DOCX document.

    Raises:
        ValueError: If python-docx is not installed.
    """
    try:
        import docx  # python-docx (optional)
    except ImportError as e:
        raise ValueError("docx extraction requires python-docx") from e
    document = docx.Document(io.BytesIO(data))
    return _text_extracted(
        "\n".join(p.text for p in document.paragraphs),
        fallback_title,
        doc_type="docx",
        extractor="python-docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


# Dispatch table keyed by lowercase file extension. Any of these formats ingests.
_EXTRACTORS = {
    ".pdf": extract_pdf,
    ".txt": extract_txt,
    ".text": extract_txt,
    ".md": extract_txt,
    ".markdown": extract_txt,
    ".html": extract_html,
    ".htm": extract_html,
    ".epub": extract_epub,
    ".docx": extract_docx,
}


def extract(data: bytes, filename: str, extension: str) -> Extracted:
    """Dispatch to the extractor registered for ``extension``.

    Args:
        data: Raw source bytes.
        filename: Original filename, used as the fallback title.
        extension: File extension (with leading dot) selecting the extractor.

    Returns:
        The extracted document.

    Raises:
        ValueError: If no extractor is registered for the extension.
    """
    fn = _EXTRACTORS.get(extension.lower())
    if fn is None:
        raise ValueError(f"unsupported document type: {extension!r}")
    return fn(data, fallback_title=filename)
