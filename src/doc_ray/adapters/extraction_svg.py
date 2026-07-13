"""SVG extraction: hardened XML parsing of <text>/<tspan> with structure inference.

Split out of :mod:`doc_ray.adapters.extraction` (D-015). Heading levels are
derived from font-size ratios against the dominant body size (BL-090), reusing
the PDF layout thresholds; native vector coordinates become ``page_runs``.
"""

from __future__ import annotations

import re as _re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace
from html import escape as _html_escape
from typing import Any

from defusedxml import ElementTree as ET

from doc_ray.adapters.extraction_common import Extracted, text_extracted
from doc_ray.domain import pdf_layout


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
    x: float | None
    y: float | None


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


def _svg_number(raw: str | None) -> float | None:
    if not raw:
        return None
    match = _re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", raw)
    if not match:
        return None
    return float(match.group(0))


def _svg_canvas_size(root: ET.Element) -> tuple[float | None, float | None]:
    view_box = root.get("viewBox") or root.get("viewbox")
    if view_box:
        numbers = [_svg_number(part) for part in _re.split(r"[,\s]+", view_box.strip()) if part]
        if len(numbers) == 4 and numbers[2] is not None and numbers[3] is not None:
            return numbers[2], numbers[3]
    return _svg_number(root.get("width")), _svg_number(root.get("height"))


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
                        x=_svg_number(style.get("x")),
                        y=_svg_number(style.get("y")),
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


def _svg_page_runs(
    root: ET.Element, runs: list[_SvgTextRun], lines: list[str]
) -> list[dict[str, Any]] | None:
    page_width, page_height = _svg_canvas_size(root)
    out: list[dict[str, Any]] = []
    for run, line in zip(runs, lines, strict=True):
        if run.x is None or run.y is None:
            continue
        font_size = run.font_size or 12.0
        bbox = (
            run.x,
            run.y - font_size,
            run.x + (len(run.text) * font_size * 0.6),
            run.y,
        )
        out.append(
            {
                "page": 0,
                "page_width": page_width,
                "page_height": page_height,
                "bbox": tuple(round(value, 6) for value in bbox),
                "text": line,
            }
        )
    return out or None


def extract_svg(data: bytes, fallback_title: str) -> Extracted:
    """Extract text from SVG <text>/<tspan> elements using hardened XML parsing."""
    root = ET.fromstring(data)
    runs = _svg_text_runs(root)
    lines = _svg_structured_lines(runs)
    extracted = text_extracted(
        "\n".join(lines),
        fallback_title,
        doc_type="svg",
        content_type="image/svg+xml",
        extractor="defusedxml-svg",
        metadata={
            "render_html": "".join(f"<p>{_html_escape(run.text)}</p>" for run in runs),
        },
    )
    return replace(
        extracted,
        title=_svg_title(root, fallback_title),
        page_runs=_svg_page_runs(root, runs, lines),
    )
