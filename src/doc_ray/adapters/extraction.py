"""Text/metadata extraction per document type. Currently: PDF.

PDFs are extracted with PyMuPDF (``extract_pdf_pymupdf``), which preserves
formatting by reconstructing structure from glyph geometry + font sizes into
Markdown (see ``ingest.pdf_layout``). pypdf remains as a flat-text fallback if
PyMuPDF is unavailable or errors on a file.
"""

from __future__ import annotations

import io
import json
import os
import posixpath
import re as _re
import shlex
import subprocess
import zipfile
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from html import escape as _html_escape
from html.parser import HTMLParser as _HTMLParser
from typing import Any, Protocol

import pypdf
from defusedxml import ElementTree as ET
from pypdf import PdfReader

from doc_ray.domain import pdf_layout


class _PyMuPDFRect(Protocol):
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
    #: Per-line source geometry (BL-076), PDF-pymupdf only: each entry is
    #: {"page": int, "bbox": (x0, y0, x1, y1), "text": str}. A sibling view over
    #: the same geometry the layout classifier already computes — it does not
    #: change full_text/to_markdown's output, only surfaces what was already
    #: being discarded. None for every other extractor.
    page_runs: list[dict[str, Any]] | None = None


# --- PyMuPDF: layout-aware, structured Markdown ----------------------------


def _env_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    return default if raw is None or raw == "" else float(raw)


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    return default if raw is None or raw == "" else int(raw)


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
        timeout=_env_int(os.environ, "DOCRAY_PDF_MATH_BACKEND_TIMEOUT", 120),
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
        level1_ratio=_env_float(source, "DOCRAY_PDF_HEADING_L1_RATIO", defaults.level1_ratio),
        level2_ratio=_env_float(source, "DOCRAY_PDF_HEADING_L2_RATIO", defaults.level2_ratio),
        level3_ratio=_env_float(source, "DOCRAY_PDF_HEADING_L3_RATIO", defaults.level3_ratio),
        bold_max_words=_env_int(
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
    for pageno, page in enumerate(doc):
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


# --- text / markup formats (byte-based, standard library) ------------------


def _text_extracted(
    text: str,
    fallback_title: str,
    *,
    doc_type: str,
    content_type: str,
    extractor: str,
    metadata: dict[str, Any] | None = None,
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
        metadata=metadata or {},
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
    _HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

    def __init__(self) -> None:
        """Initialize the parser with charref conversion enabled."""
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Track skip depth for hidden tags and emit breaks for block tags."""
        tag = tag.lower()
        if tag in self._SKIP:
            self._skip += 1
        elif tag in self._HEADINGS:
            self.parts.append(f"\n{'#' * self._HEADINGS[tag]} ")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        """Unwind skip depth and emit a trailing break for block tags."""
        tag = tag.lower()
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
    raw = "".join(p.parts)
    return "\n".join(line.strip() for line in raw.splitlines() if line.strip())


class _HTMLRender(_HTMLParser):
    """Sanitize a small structural HTML subset for the non-paginated original view."""

    _SKIP = {"script", "style", "head", "title", "meta", "link"}
    _TAGS = {
        "a",
        "blockquote",
        "br",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "ol",
        "p",
        "strong",
        "ul",
    }
    _MAP = {"b": "strong", "i": "em"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self._stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._SKIP:
            self._skip += 1
            return
        tag = self._MAP.get(tag, tag)
        if self._skip or tag not in self._TAGS:
            return
        if tag == "br":
            self.parts.append("<br>")
            return
        if tag == "a":
            href = next((v for k, v in attrs if k == "href" and v), "")
            if href.startswith(("http://", "https://", "#", "/")):
                self.parts.append(f'<a href="{_html_escape(href, quote=True)}">')
                self._stack.append(tag)
            return
        self.parts.append(f"<{tag}>")
        self._stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = self._MAP.get(tag.lower(), tag.lower())
        if tag in self._SKIP and self._skip:
            self._skip -= 1
            return
        if self._skip or tag not in self._TAGS or tag == "br":
            return
        if tag in self._stack:
            while self._stack:
                open_tag = self._stack.pop()
                self.parts.append(f"</{open_tag}>")
                if open_tag == tag:
                    break

    def handle_data(self, data: str) -> None:
        if self._skip or not self._stack or not data.strip():
            return
        self.parts.append(_html_escape(data, quote=False))

    def close(self) -> None:
        super().close()
        while self._stack:
            self.parts.append(f"</{self._stack.pop()}>")


def _html_to_render_html(html: str) -> str:
    """Return sanitized structural HTML for the non-paginated original view."""
    p = _HTMLRender()
    p.feed(html)
    p.close()
    return "".join(p.parts)


def extract_html(data: bytes, fallback_title: str) -> Extracted:
    """Extract visible text from an HTML document.

    Args:
        data: Raw HTML bytes.
        fallback_title: Title for the extracted document.

    Returns:
        The extracted HTML document.
    """
    html = data.decode("utf-8", "replace")
    return _text_extracted(
        _html_to_text(html),
        fallback_title,
        doc_type="html",
        content_type="text/html",
        extractor="html.parser",
        metadata={"render_html": _html_to_render_html(html)},
    )


def _max_zip_uncompressed_bytes() -> int:
    raw = os.environ.get("DOCRAY_EXTRACT_MAX_UNCOMPRESSED_MB", "").strip()
    mb = int(raw) if raw else 500
    return mb * 1024 * 1024


def _check_zip_bomb_bounds(zf: zipfile.ZipFile) -> None:
    """Reject a zip whose total *uncompressed* size exceeds a sane cap (BL-088).

    DOCX and EPUB are both zip containers with no guard against a classic
    decompression bomb: a small compressed file whose declared uncompressed
    content would exhaust memory before any XML parsing even begins.
    Overridable via ``DOCRAY_EXTRACT_MAX_UNCOMPRESSED_MB`` (default 500 MiB).
    """
    total = sum(info.file_size for info in zf.infolist())
    limit = _max_zip_uncompressed_bytes()
    if total > limit:
        raise ValueError(
            f"archive's uncompressed size ({total} bytes) exceeds the {limit}-byte limit"
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
    render_parts: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        _check_zip_bomb_bounds(zf)
        for name in _epub_spine(zf):
            try:
                html = zf.read(name).decode("utf-8", "replace")
                parts.append(_html_to_text(html))
                render_parts.append(_html_to_render_html(html))
            except KeyError:
                continue
    return _text_extracted(
        "\n".join(parts),
        fallback_title,
        doc_type="epub",
        content_type="application/epub+zip",
        extractor="stdlib-epub",
        metadata={"render_html": "".join(render_parts)},
    )


def _xml_local_name(tag: str) -> str:
    """Return an XML tag's local name without its namespace URI."""
    return tag.rsplit("}", 1)[-1] if tag.startswith("{") else tag


def _xml_text_content(elem: ET.Element) -> str:
    """Return visible text under an XML element, skipping executable/style nodes."""
    if _xml_local_name(elem.tag).lower() in {"script", "style"}:
        return ""
    parts: list[str] = []
    if elem.text:
        parts.append(elem.text)
    for child in elem:
        parts.append(_xml_text_content(child))
        if child.tail:
            parts.append(child.tail)
    return " ".join("".join(parts).split())


@dataclass(frozen=True)
class _SvgTextRun:
    """Visible SVG text plus enough style metadata to classify structure."""

    text: str
    font_size: float | None
    bold: bool


def _svg_style_declarations(raw: str | None) -> dict[str, str]:
    declarations: dict[str, str] = {}
    if not raw:
        return declarations
    for part in raw.split(";"):
        key, sep, value = part.partition(":")
        if sep and key.strip() and value.strip():
            declarations[key.strip().lower()] = value.strip()
    return declarations


def _svg_effective_style(elem: ET.Element, inherited: Mapping[str, str]) -> dict[str, str]:
    style = {k.lower(): v for k, v in inherited.items()}
    for key, value in elem.attrib.items():
        style[_xml_local_name(key).lower()] = value
    style.update(_svg_style_declarations(style.get("style")))
    return style


def _svg_font_size(style: Mapping[str, str]) -> float | None:
    raw = style.get("font-size")
    if not raw:
        return None
    match = _re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", raw)
    if not match:
        return None
    size = float(match.group(0))
    return size if size > 0 else None


def _svg_font_bold(style: Mapping[str, str]) -> bool:
    raw = (style.get("font-weight") or "").strip().lower()
    if raw in {"bold", "bolder"}:
        return True
    if raw.isdigit():
        return int(raw) >= 600
    return False


def _svg_title(root: ET.Element, fallback_title: str) -> str:
    for elem in root.iter():
        if _xml_local_name(elem.tag).lower() == "title":
            title = _xml_text_content(elem)
            if title:
                return title
    return fallback_title


def _svg_text_runs(root: ET.Element) -> list[_SvgTextRun]:
    """Extract text runs from SVG <text> nodes, including nested <tspan> text."""
    runs: list[_SvgTextRun] = []

    def visit(
        elem: ET.Element,
        *,
        inherited: Mapping[str, str] | None = None,
        inside_text: bool = False,
    ) -> None:
        name = _xml_local_name(elem.tag).lower()
        if name in {"script", "style", "title"}:
            return
        style = _svg_effective_style(elem, inherited or {})
        if name == "text" or (name == "tspan" and not inside_text):
            text = _xml_text_content(elem)
            if text:
                runs.append(
                    _SvgTextRun(
                        text=text,
                        font_size=_svg_font_size(style),
                        bold=_svg_font_bold(style),
                    )
                )
            return
        for child in elem:
            visit(
                child,
                inherited=style,
                inside_text=inside_text or name in {"text", "tspan"},
            )

    visit(root)
    return runs


def _svg_body_font_size(runs: list[_SvgTextRun]) -> float | None:
    sizes = [run.font_size for run in runs if run.font_size is not None]
    if not sizes:
        return None
    counts = Counter(sizes)
    return counts.most_common(1)[0][0]


def _svg_structured_lines(runs: list[_SvgTextRun]) -> list[str]:
    body = _svg_body_font_size(runs)
    lines: list[str] = []
    thresholds = pdf_layout.DEFAULT_HEADING_THRESHOLDS
    for run in runs:
        level = (
            pdf_layout.heading_level(run.font_size, body, thresholds)
            if run.font_size is not None and body is not None
            else 0
        )
        if level == 0 and run.bold and len(run.text.split()) <= thresholds.bold_max_words:
            level = 3
        prefix = f"{'#' * level} " if level else ""
        lines.append(f"{prefix}{run.text}")
    return lines


def extract_svg(data: bytes, fallback_title: str) -> Extracted:
    """Extract text from SVG <text>/<tspan> elements using hardened XML parsing."""
    root = ET.fromstring(data)
    runs = _svg_text_runs(root)
    lines = _svg_structured_lines(runs)
    extracted = _text_extracted(
        "\n".join(lines),
        fallback_title,
        doc_type="svg",
        content_type="image/svg+xml",
        extractor="defusedxml-svg",
        metadata={
            "render_html": "".join(f"<p>{_html_escape(run.text)}</p>" for run in runs),
        },
    )
    return replace(extracted, title=_svg_title(root, fallback_title))


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
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        _check_zip_bomb_bounds(zf)
    document = docx.Document(io.BytesIO(data))
    paragraphs = [p.text for p in document.paragraphs if p.text]
    return _text_extracted(
        "\n".join(paragraphs),
        fallback_title,
        doc_type="docx",
        extractor="python-docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        metadata={
            "render_html": "".join(f"<p>{_html_escape(text)}</p>" for text in paragraphs)
        },
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
    ".svg": extract_svg,
    ".docx": extract_docx,
}

_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".text": "text/plain",
    ".md": "text/plain",
    ".markdown": "text/plain",
    ".html": "text/html",
    ".htm": "text/html",
    ".epub": "application/epub+zip",
    ".svg": "image/svg+xml",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def is_supported_extension(extension: str) -> bool:
    """Return whether ``extension`` (with leading dot) has a registered extractor.

    The same allowlist :func:`extract` dispatches on — exposed so callers (e.g.
    ``ingestDocument``, BL-106) can reject an unsupported upload before ever
    persisting its blob, not just before extraction runs.
    """
    return extension.lower() in _EXTRACTORS


def content_type_for_extension(extension: str) -> str:
    """Return the persisted source Content-Type for a supported extension."""
    try:
        return _CONTENT_TYPES[extension.lower()]
    except KeyError as exc:
        raise ValueError(f"unsupported document type: {extension!r}") from exc


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
    return _sanitize(fn(data, fallback_title=filename))


def _sanitize(extracted: Extracted) -> Extracted:
    """Strip NUL (0x00) bytes from every extracted string.

    Real-world PDFs emit NULs in extracted text (embedded glyph/binary
    artifacts), and PostgreSQL ``text``/``jsonb`` values categorically reject
    them — one NUL fails the whole ingest. Sanitizing at the dispatcher covers
    every format with one chokepoint.
    """

    def clean(value: str) -> str:
        return value.replace("\x00", "") if "\x00" in value else value

    return replace(
        extracted,
        title=clean(extracted.title),
        author=clean(extracted.author) if extracted.author else extracted.author,
        full_text=clean(extracted.full_text),
        metadata={k: clean(v) if isinstance(v, str) else v for k, v in extracted.metadata.items()},
    )
