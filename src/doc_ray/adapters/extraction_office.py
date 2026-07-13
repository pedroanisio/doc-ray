"""DOCX/PPTX extraction via the optional python-docx / python-pptx packages.

Split out of :mod:`doc_ray.adapters.extraction` (D-015). Both formats are zip
containers and pass the shared decompression-bomb guard (BL-088) before any
XML parsing; document structure (headings, lists, slide placeholders) is
derived from real format-native metadata rather than inference (BL-089/BL-091).
"""

from __future__ import annotations

import io
import re as _re
import zipfile
from dataclasses import replace
from html import escape as _html_escape
from typing import Any

from doc_ray.adapters.extraction_common import (
    Extracted,
    check_zip_bomb_bounds,
    text_extracted,
)


def _docx_style_name(paragraph: object) -> str:
    style = getattr(paragraph, "style", None)
    return str(getattr(style, "name", "") or "").strip()


def _docx_structured_line(text: str, style_name: str) -> str:
    normalized = _re.sub(r"\s+", " ", style_name.strip().lower())
    heading_match = _re.fullmatch(r"heading ([1-6])", normalized)
    if heading_match:
        return f"{'#' * int(heading_match.group(1))} {text}"
    if normalized.startswith(("list bullet", "list number")):
        return f"- {text}"
    return text


def _pptx_placeholder_kind(shape: object) -> str:
    if not getattr(shape, "is_placeholder", False):
        return ""
    placeholder = getattr(shape, "placeholder_format", None)
    return str(getattr(placeholder, "type", "") or "").upper()


def _pptx_paragraph_text(paragraph: object) -> str:
    return str(getattr(paragraph, "text", "") or "").strip()


def _pptx_structured_line(text: str, placeholder_kind: str, paragraph: object) -> str:
    if "TITLE" in placeholder_kind:
        return f"# {text}"
    level = max(int(getattr(paragraph, "level", 0) or 0), 0)
    indent = "  " * level
    return f"{indent}- {text}"


def _pptx_float_attr(obj: object, name: str) -> float | None:
    value = getattr(obj, name, None)
    if value is None:
        return None
    return float(value)


def _pptx_shape_bbox(shape: object) -> tuple[float, float, float, float] | None:
    left = _pptx_float_attr(shape, "left")
    top = _pptx_float_attr(shape, "top")
    width = _pptx_float_attr(shape, "width")
    height = _pptx_float_attr(shape, "height")
    if left is None or top is None or width is None or height is None:
        return None
    return (left, top, left + width, top + height)


def extract_pptx(data: bytes, fallback_title: str) -> Extracted:
    """Extract slide text from a PPTX using python-pptx.

    Args:
        data: Raw PPTX bytes.
        fallback_title: Title for the extracted document.

    Returns:
        The extracted PPTX document.

    Raises:
        ValueError: If python-pptx is not installed.
    """
    try:
        import pptx  # python-pptx (optional)
    except ImportError as e:
        raise ValueError("pptx extraction requires python-pptx") from e
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        check_zip_bomb_bounds(zf)
    presentation = pptx.Presentation(io.BytesIO(data))
    lines: list[str] = []
    render_parts: list[str] = []
    page_runs: list[dict[str, Any]] = []
    page_width = _pptx_float_attr(presentation, "slide_width")
    page_height = _pptx_float_attr(presentation, "slide_height")
    for page, slide in enumerate(presentation.slides):
        for shape in getattr(slide, "shapes", []):
            if not getattr(shape, "has_text_frame", False):
                continue
            placeholder_kind = _pptx_placeholder_kind(shape)
            bbox = _pptx_shape_bbox(shape)
            for paragraph in getattr(shape.text_frame, "paragraphs", []):
                text = _pptx_paragraph_text(paragraph)
                if not text:
                    continue
                line = _pptx_structured_line(text, placeholder_kind, paragraph)
                lines.append(line)
                render_parts.append(f"<p>{_html_escape(text)}</p>")
                if bbox is not None:
                    page_runs.append(
                        {
                            "page": page,
                            "page_width": page_width,
                            "page_height": page_height,
                            "bbox": bbox,
                            "text": line,
                        }
                    )
        if getattr(slide, "has_notes_slide", False):
            notes_frame = getattr(slide.notes_slide, "notes_text_frame", None)
            for paragraph in getattr(notes_frame, "paragraphs", []):
                text = _pptx_paragraph_text(paragraph)
                if text:
                    lines.append(text)
                    render_parts.append(f"<p>{_html_escape(text)}</p>")
    extracted = text_extracted(
        "\n".join(lines),
        fallback_title,
        doc_type="pptx",
        extractor="python-pptx",
        content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        metadata={"render_html": "".join(render_parts)},
    )
    return replace(extracted, page_runs=page_runs or None)


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
        check_zip_bomb_bounds(zf)
    document = docx.Document(io.BytesIO(data))
    paragraphs = [
        (_docx_structured_line(p.text, _docx_style_name(p)), p.text)
        for p in document.paragraphs
        if p.text
    ]
    return text_extracted(
        "\n".join(line for line, _text in paragraphs),
        fallback_title,
        doc_type="docx",
        extractor="python-docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        metadata={
            "render_html": "".join(f"<p>{_html_escape(text)}</p>" for _line, text in paragraphs)
        },
    )
