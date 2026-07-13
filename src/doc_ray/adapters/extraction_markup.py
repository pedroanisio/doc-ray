"""Text/HTML/EPUB extraction: stdlib parsers with hardened zip/XML handling.

Split out of :mod:`doc_ray.adapters.extraction` (D-015). The HTML paths feed
both plain text (``full_text``) and the sanitized ``render_html`` used by the
webapp's non-paginated original view (BL-086); EPUB reuses them per spine
document behind the shared zip-bomb guard (BL-088).
"""

from __future__ import annotations

import io
import posixpath
import re as _re
import zipfile
from html import escape as _html_escape
from html.parser import HTMLParser as _HTMLParser

from defusedxml import ElementTree as ET

from doc_ray.adapters.extraction_common import (
    Extracted,
    check_zip_bomb_bounds,
    text_extracted,
)


def extract_txt(data: bytes, fallback_title: str) -> Extracted:
    """Extract UTF-8 plain text.

    Args:
        data: Raw text bytes.
        fallback_title: Title for the extracted document.

    Returns:
        The extracted plain-text document.
    """
    return text_extracted(
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


def html_to_text(html: str) -> str:
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
        """Initialize the sanitizer with charref conversion enabled."""
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self._stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Emit allow-listed structural tags, dropping everything else."""
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
        """Close allow-listed tags, unwinding any unbalanced nesting."""
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
        """Emit escaped character data inside open allow-listed tags."""
        if self._skip or not self._stack or not data.strip():
            return
        self.parts.append(_html_escape(data, quote=False))

    def close(self) -> None:
        """Flush the parser and close any tags left open by the source."""
        super().close()
        while self._stack:
            self.parts.append(f"</{self._stack.pop()}>")


def html_to_render_html(html: str) -> str:
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
    return text_extracted(
        html_to_text(html),
        fallback_title,
        doc_type="html",
        content_type="text/html",
        extractor="html.parser",
        metadata={"render_html": html_to_render_html(html)},
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
        check_zip_bomb_bounds(zf)
        for name in _epub_spine(zf):
            try:
                html = zf.read(name).decode("utf-8", "replace")
                parts.append(html_to_text(html))
                render_parts.append(html_to_render_html(html))
            except KeyError:
                continue
    return text_extracted(
        "\n".join(parts),
        fallback_title,
        doc_type="epub",
        content_type="application/epub+zip",
        extractor="stdlib-epub",
        metadata={"render_html": "".join(render_parts)},
    )
