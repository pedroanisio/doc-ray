"""Turn PyMuPDF span geometry + font sizes into structured Markdown.

pypdf's ``extract_text`` discards the two signals that carry formatting — glyph
x/y positions and font size/weight. PyMuPDF keeps them (``page.get_text("dict")``
yields blocks → lines → spans with ``bbox``, ``size``, ``flags``). These pure
functions reconstruct structure from that geometry:

  * word spacing from inter-span gaps        (``line_text``)
  * headings from relative font size / weight (``heading_level`` / ``classify_line``)
  * bullet lists                              (``is_bullet`` / ``strip_bullet``)
  * de-hyphenation across line breaks         (``dehyphenate_join``)
  * running header/footer removal             (``strip_marginals``)
  * assembly to Markdown                      (``to_markdown``)

Everything here is dependency-free and takes plain dicts, so it is unit-tested
without a PDF; ``ingest.extract`` provides the thin fitz adapter.

A *span* is ``{"text", "x0", "x1", "size", "bold"}``; a *line* adds
``{"spans", "y0", "y1", "page", "page_h"}``; a *block* is ``{"lines": [...]}``.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

_BULLET_RE = re.compile(r"^\s*(?:[•‣◦·▪]\s*|[*\-–—]\s+)")
_HYPHEN_END = re.compile(r"[^\W\d_]-$", re.UNICODE)
_SENT_END = tuple(".!?;:,")
# C0 control characters except tab/newline/carriage-return. NUL in particular
# cannot be stored in a PostgreSQL text column, so it must never reach the DB.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


@dataclass(frozen=True)
class HeadingThresholds:
    """Corpus-tunable thresholds for PDF line classification."""

    level1_ratio: float = 1.5
    level2_ratio: float = 1.25
    level3_ratio: float = 1.1
    bold_max_words: int = 10

    def __post_init__(self) -> None:
        """Validate monotonic heading ratios and bold-heading bounds."""
        if not (self.level1_ratio >= self.level2_ratio >= self.level3_ratio > 0):
            raise ValueError("heading ratios must satisfy level1 >= level2 >= level3 > 0")
        if self.bold_max_words < 1:
            raise ValueError("bold_max_words must be at least 1")


DEFAULT_HEADING_THRESHOLDS = HeadingThresholds()


def sanitize_text(text: str) -> str:
    r"""Strip control characters (incl. NUL) that PDFs emit; keep \t and \n."""
    return _CONTROL_RE.sub("", text)


# --- line reconstruction ---------------------------------------------------


def line_text(spans: list[dict[str, Any]], space_ratio: float = 0.2) -> str:
    """Join spans, inserting a space where the horizontal gap implies one.

    Args:
        spans: The span dicts of a single line, left to right.
        space_ratio: Gap threshold, as a fraction of font size, above which a
            space is inserted between adjacent spans.

    Returns:
        The reconstructed line text, stripped of leading/trailing whitespace.
    """
    out = ""
    prev = None
    for sp in spans:
        if prev is not None:
            gap = sp["x0"] - prev["x1"]
            wide = gap > space_ratio * max(prev["size"], sp["size"])
            if wide and not out.endswith(" ") and not sp["text"].startswith(" "):
                out += " "
        out += sp["text"]
        prev = sp
    return out.strip()


# --- typographic classification -------------------------------------------


def heading_level(size: float, body: float, thresholds: HeadingThresholds | None = None) -> int:
    """Heading level 1–3 from size relative to body text; 0 if not a heading."""
    if body <= 0:
        return 0
    thresholds = thresholds or DEFAULT_HEADING_THRESHOLDS
    r = size / body
    if r >= thresholds.level1_ratio:
        return 1
    if r >= thresholds.level2_ratio:
        return 2
    if r >= thresholds.level3_ratio:
        return 3
    return 0


def is_bullet(text: str) -> bool:
    """Report whether ``text`` begins with a list-bullet marker."""
    return bool(_BULLET_RE.match(text))


def strip_bullet(text: str) -> str:
    """Remove a leading bullet marker from ``text`` and strip whitespace."""
    return _BULLET_RE.sub("", text, count=1).strip()


def classify_line(
    text: str,
    size: float,
    bold: bool,
    body: float,
    thresholds: HeadingThresholds | None = None,
) -> tuple[str, int]:
    """Classify a line as ('heading', level) | ('list', 0) | ('para', 0)."""
    t = text.strip()
    if is_bullet(t):
        return ("list", 0)
    thresholds = thresholds or DEFAULT_HEADING_THRESHOLDS
    lvl = heading_level(size, body, thresholds)
    if lvl:
        return ("heading", lvl)
    # A short, bold line that doesn't read like a sentence is a heading.
    if bold and t and len(t.split()) <= thresholds.bold_max_words and t[-1] not in _SENT_END:
        return ("heading", 3)
    return ("para", 0)


def dehyphenate_join(a: str, b: str) -> str:
    """Join two line fragments, healing a word split by a line-break hyphen."""
    if _HYPHEN_END.search(a) and b[:1].isalpha():
        return a[:-1] + b
    return a + " " + b


# --- document-level analysis ----------------------------------------------


def body_size(blocks: list[dict[str, Any]]) -> float:
    """Return the dominant text size (char-weighted) — the body font size.

    Args:
        blocks: The block dicts to inspect.

    Returns:
        The most common rounded font size, or ``0.0`` if there is no text.
    """
    weight: Counter[float] = Counter()
    for b in blocks:
        for ln in b["lines"]:
            for sp in ln["spans"]:
                weight[round(sp["size"], 1)] += max(len(sp["text"].strip()), 1)
    if not weight:
        return 0.0
    return float(max(weight.items(), key=lambda kv: kv[1])[0])


def page_runs_from_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one ``{page, bbox, text}`` entry per line (BL-076).

    A sibling pass over the same ``blocks`` structure ``to_markdown`` consumes —
    every line already carries its source ``page`` and ``y0``/``y1``; this just
    also collects the line's x-range from its spans and its concatenated text,
    without touching the Markdown classifier itself.
    """
    runs: list[dict[str, Any]] = []
    for block in blocks:
        for line in block["lines"]:
            spans = line["spans"]
            if not spans:
                continue
            runs.append(
                {
                    "page": line["page"],
                    "bbox": (
                        min(sp["x0"] for sp in spans),
                        line["y0"],
                        max(sp["x1"] for sp in spans),
                        line["y1"],
                    ),
                    "text": "".join(sp["text"] for sp in spans),
                }
            )
    return runs


def _norm_marginal(text: str) -> str:
    return re.sub(r"\d+", "#", text).lower().strip()


def strip_marginals(
    blocks: list[dict[str, Any]], top: float = 0.1, bottom: float = 0.9
) -> list[dict[str, Any]]:
    """Drop lines in the top/bottom page bands whose text recurs across pages.

    Args:
        blocks: The block dicts to filter.
        top: Upper page fraction treated as the header band.
        bottom: Lower page-fraction boundary of the footer band.

    Returns:
        New block dicts with recurring header/footer lines removed and empty
        blocks dropped.
    """
    pages: set[int] = set()
    key_pages: dict[str, set[int]] = defaultdict(set)
    marginal: list[tuple[int, int, str]] = []
    for bi, b in enumerate(blocks):
        for li, ln in enumerate(b["lines"]):
            pages.add(ln["page"])
            ph = ln.get("page_h") or 0
            in_band = ph and (ln["y1"] <= top * ph or ln["y0"] >= bottom * ph)
            if in_band:
                key = _norm_marginal(line_text(ln["spans"]))
                key_pages[key].add(ln["page"])
                marginal.append((bi, li, key))
    n = max(1, len(pages))
    threshold = max(2, (n + 1) // 2)
    drop = {(bi, li) for bi, li, key in marginal if len(key_pages[key]) >= threshold}
    out = []
    for bi, b in enumerate(blocks):
        lines = [ln for li, ln in enumerate(b["lines"]) if (bi, li) not in drop]
        if lines:
            out.append({"lines": lines})
    return out


# --- assembly --------------------------------------------------------------


def _mostly_bold(spans: list[dict[str, Any]]) -> bool:
    bold = sum(len(s["text"]) for s in spans if s.get("bold"))
    total = sum(len(s["text"]) for s in spans) or 1
    return bold * 2 >= total


def block_markdown(
    block: dict[str, Any], body: float, thresholds: HeadingThresholds | None = None
) -> str:
    """Render one block as a Markdown paragraph, heading, or bullet list.

    Args:
        block: The block dict to render.
        body: The body font size, used to classify heading levels.
        thresholds: Optional corpus-specific heading thresholds.

    Returns:
        The Markdown for the block, or an empty string if it has no text.
    """
    infos: list[tuple[str, int, str]] = []
    for ln in block["lines"]:
        text = line_text(ln["spans"])
        size = max((sp["size"] for sp in ln["spans"]), default=body)
        kind, lvl = classify_line(text, size, _mostly_bold(ln["spans"]), body, thresholds)
        if text:
            infos.append((kind, lvl, text))
    if not infos:
        return ""

    if any(k == "list" for k, _, _ in infos):
        return "\n".join("- " + (strip_bullet(t) if k == "list" else t) for k, _, t in infos)
    if infos[0][0] == "heading" and len(infos) <= 2:
        text = " ".join(t for _, _, t in infos)
        return "#" * infos[0][1] + " " + text
    para = ""
    for _, _, text in infos:
        para = text if not para else dehyphenate_join(para, text)
    return para


def to_markdown(
    blocks: list[dict[str, Any]],
    body: float | None = None,
    thresholds: HeadingThresholds | None = None,
) -> str:
    """Assemble blocks into a Markdown document, structure preserved.

    Args:
        blocks: The block dicts to assemble.
        body: The body font size; inferred from ``blocks`` if omitted.
        thresholds: Optional corpus-specific heading thresholds.

    Returns:
        The Markdown document, or an empty string if there is no content.
    """
    blocks = strip_marginals(blocks)
    if body is None:
        body = body_size(blocks) or 10.0
    parts = [block_markdown(b, body, thresholds) for b in blocks]
    md = "\n\n".join(p for p in parts if p)
    return sanitize_text(md.strip() + "\n") if md else ""
