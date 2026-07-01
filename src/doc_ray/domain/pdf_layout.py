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

_BULLET_RE = re.compile(r"^\s*(?:[•‣◦·▪]\s*|[*\-–—]\s+)")
_HYPHEN_END = re.compile(r"[^\W\d_]-$", re.UNICODE)
_SENT_END = tuple(".!?;:,")
# C0 control characters except tab/newline/carriage-return. NUL in particular
# cannot be stored in a PostgreSQL text column, so it must never reach the DB.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def sanitize_text(text: str) -> str:
    """Strip control characters (incl. NUL) that PDFs emit; keep \\t and \\n."""
    return _CONTROL_RE.sub("", text)


# --- line reconstruction ---------------------------------------------------

def line_text(spans: list[dict], space_ratio: float = 0.2) -> str:
    """Join spans, inserting a space where the horizontal gap implies one."""
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

def heading_level(size: float, body: float) -> int:
    """Heading level 1–3 from size relative to body text; 0 if not a heading."""
    if body <= 0:
        return 0
    r = size / body
    if r >= 1.5:
        return 1
    if r >= 1.25:
        return 2
    if r >= 1.1:
        return 3
    return 0


def is_bullet(text: str) -> bool:
    return bool(_BULLET_RE.match(text))


def strip_bullet(text: str) -> str:
    return _BULLET_RE.sub("", text, count=1).strip()


def classify_line(text: str, size: float, bold: bool, body: float) -> tuple[str, int]:
    """Classify a line as ('heading', level) | ('list', 0) | ('para', 0)."""
    t = text.strip()
    if is_bullet(t):
        return ("list", 0)
    lvl = heading_level(size, body)
    if lvl:
        return ("heading", lvl)
    # A short, bold line that doesn't read like a sentence is a heading.
    if bold and t and len(t.split()) <= 10 and t[-1] not in _SENT_END:
        return ("heading", 3)
    return ("para", 0)


def dehyphenate_join(a: str, b: str) -> str:
    """Join two line fragments, healing a word split by a line-break hyphen."""
    if _HYPHEN_END.search(a) and b[:1].isalpha():
        return a[:-1] + b
    return a + " " + b


# --- document-level analysis ----------------------------------------------

def body_size(blocks: list[dict]) -> float:
    """The dominant text size (weighted by characters) — the body font size."""
    weight: Counter = Counter()
    for b in blocks:
        for ln in b["lines"]:
            for sp in ln["spans"]:
                weight[round(sp["size"], 1)] += max(len(sp["text"].strip()), 1)
    if not weight:
        return 0.0
    return max(weight.items(), key=lambda kv: kv[1])[0]


def _norm_marginal(text: str) -> str:
    return re.sub(r"\d+", "#", text).lower().strip()


def strip_marginals(blocks: list[dict], top: float = 0.1, bottom: float = 0.9) -> list[dict]:
    """Drop lines in the top/bottom page bands whose text recurs across pages."""
    pages: set = set()
    key_pages: dict = defaultdict(set)
    marginal: list = []
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

def _mostly_bold(spans: list[dict]) -> bool:
    bold = sum(len(s["text"]) for s in spans if s.get("bold"))
    total = sum(len(s["text"]) for s in spans) or 1
    return bold * 2 >= total


def block_markdown(block: dict, body: float) -> str:
    infos = []
    for ln in block["lines"]:
        text = line_text(ln["spans"])
        size = max((sp["size"] for sp in ln["spans"]), default=body)
        kind, lvl = classify_line(text, size, _mostly_bold(ln["spans"]), body)
        if text:
            infos.append((kind, lvl, text))
    if not infos:
        return ""

    if any(k == "list" for k, _, _ in infos):
        return "\n".join(
            "- " + (strip_bullet(t) if k == "list" else t) for k, _, t in infos
        )
    if infos[0][0] == "heading" and len(infos) <= 2:
        text = " ".join(t for _, _, t in infos)
        return "#" * infos[0][1] + " " + text
    para = ""
    for _, _, text in infos:
        para = text if not para else dehyphenate_join(para, text)
    return para


def to_markdown(blocks: list[dict], body: float | None = None) -> str:
    """Assemble blocks into a Markdown document, structure preserved."""
    blocks = strip_marginals(blocks)
    if body is None:
        body = body_size(blocks) or 10.0
    parts = [block_markdown(b, body) for b in blocks]
    md = "\n\n".join(p for p in parts if p)
    return sanitize_text(md.strip() + "\n") if md else ""
