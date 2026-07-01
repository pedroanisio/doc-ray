"""Prototype: structural heading extraction from PDFs.

Standalone (no DB). Run:  python proto/headings.py <file-or-dir> [--debug]
"""

from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF


# --- numbering / structural text patterns ---------------------------------
RE_DOTNUM = re.compile(r"^(\d+(?:\.\d+){0,5})[.)]?\s+\S")           # 1  1.2  1.2.3
RE_KEYWORD = re.compile(
    r"^(chapter|section|part|appendix|annex|cap[íi]tulo|se[çc][ãa]o|parte|ap[êe]ndice)\b",
    re.IGNORECASE,
)
RE_ALLCAPS = re.compile(r"^[^a-z]{4,}$")                             # no lowercase, 4+ chars
RE_SENTENCE_END = re.compile(r"[.?!;:,]$")
RE_PAGENUM_PREFIX = re.compile(r"^\d{1,4}\s*[-–—]\s+\S")             # "006 - Capítulo" (printed TOC)


def _letters(s: str) -> int:
    return sum(c.isalpha() for c in s)


@dataclass
class Line:
    text: str
    size: float          # max span size on the line
    bold: bool
    font: str
    page: int
    y0: float
    y1: float
    x0: float
    x1: float


@dataclass
class Heading:
    level: int
    text: str
    page: int
    ordinal: int
    confidence: float
    signals: dict = field(default_factory=dict)


# --- Stage 1: extract lines with typography -------------------------------
def extract_lines(doc: fitz.Document) -> list[Line]:
    lines: list[Line] = []
    for pno, page in enumerate(doc):
        d = page.get_text("dict")
        for block in d.get("blocks", []):
            if block.get("type") != 0:  # 0 = text
                continue
            for ln in block.get("lines", []):
                spans = ln.get("spans", [])
                text = "".join(s["text"] for s in spans).strip()
                if not text:
                    continue
                # weight typography by char count so a stray big glyph doesn't win
                sizes = [(round(s["size"], 1), len(s["text"])) for s in spans if s["text"].strip()]
                if not sizes:
                    continue
                max_size = max(sz for sz, _ in sizes)
                bold_chars = sum(len(s["text"]) for s in spans if _is_bold(s))
                total_chars = sum(len(s["text"]) for s in spans) or 1
                x0 = min(s["bbox"][0] for s in spans)
                x1 = max(s["bbox"][2] for s in spans)
                lines.append(
                    Line(
                        text=text,
                        size=max_size,
                        bold=bold_chars / total_chars >= 0.6,
                        font=spans[0]["font"],
                        page=pno,
                        y0=ln["bbox"][1],
                        y1=ln["bbox"][3],
                        x0=x0,
                        x1=x1,
                    )
                )
    return lines


def _is_bold(span: dict) -> bool:
    if span.get("flags", 0) & 2 ** 4:  # bit 4 = bold in PyMuPDF
        return True
    return "bold" in span.get("font", "").lower() or "black" in span.get("font", "").lower()


# --- Stage 1b: drop repeating headers/footers -----------------------------
def strip_running_heads(lines: list[Line], n_pages: int) -> list[Line]:
    if n_pages < 3:
        return lines
    # signature: normalized text ignoring digits (page numbers vary)
    counts: Counter[str] = Counter()
    for ln in lines:
        sig = re.sub(r"\d+", "#", ln.text.lower())
        counts[sig] += 1
    threshold = max(3, 0.4 * n_pages)
    repeating = {sig for sig, c in counts.items() if c >= threshold}
    return [ln for ln in lines if re.sub(r"\d+", "#", ln.text.lower()) not in repeating]


# --- Stage 2: body-text baseline ------------------------------------------
def body_size(lines: list[Line]) -> float:
    weighted: Counter[float] = Counter()
    for ln in lines:
        weighted[ln.size] += len(ln.text)
    return weighted.most_common(1)[0][0] if weighted else 12.0


# --- Stage 3: score candidates --------------------------------------------
def score_line(ln: Line, s_body: float, median_leading: float, col_width: float,
               prev_gap: float) -> tuple[float, dict]:
    size_ratio = ln.size / s_body if s_body else 1.0
    word_count = len(ln.text.split())
    is_short = word_count <= 14 and (ln.x1 - ln.x0) < 0.7 * col_width
    gap_above = prev_gap > 1.5 * median_leading if median_leading else False
    num = bool(RE_DOTNUM.match(ln.text) or RE_KEYWORD.match(ln.text))
    allcaps = bool(RE_ALLCAPS.match(ln.text)) and word_count <= 12
    not_prose = not RE_SENTENCE_END.search(ln.text)

    signals = {
        "size_ratio": round(size_ratio, 2),
        "bold": ln.bold,
        "short": is_short,
        "gap": gap_above,
        "num": num,
        "allcaps": allcaps,
    }
    score = (
        0.40 * (size_ratio >= 1.15)
        + 0.20 * ln.bold
        + 0.15 * is_short
        + 0.10 * gap_above
        + 0.15 * (num or allcaps)
    )
    if not not_prose:
        score *= 0.5  # ends like a sentence -> probably body
    return score, signals


# --- Stage 4/5: level assignment + tree -----------------------------------
def extract_headings(doc: fitz.Document, debug: bool = False) -> list[Heading]:
    # Fast path: embedded outline
    toc = doc.get_toc(simple=True)
    if toc and len({e[2] for e in toc}) > 1:
        return [
            Heading(level=min(lvl, 4), text=title.strip(), page=page - 1,
                    ordinal=i, confidence=1.0, signals={"source": "toc"})
            for i, (lvl, title, page) in enumerate(toc)
        ]

    lines = extract_lines(doc)
    lines = strip_running_heads(lines, doc.page_count)
    if not lines:
        return []

    s_body = body_size(lines)
    leadings = _leadings(lines)
    median_leading = sorted(leadings)[len(leadings) // 2] if leadings else 0.0
    col_width = max(ln.x1 - ln.x0 for ln in lines)

    # score
    candidates: list[tuple[Line, float, dict]] = []
    prev = None
    for ln in lines:
        gap = (ln.y0 - prev.y1) if (prev and prev.page == ln.page) else median_leading
        prev = ln
        # Guard A: reject junk (single letters, bare page numbers, dividers).
        if _letters(ln.text) < 3:
            continue
        # Guard D: printed table-of-contents rows ("006 - Capítulo 1").
        if RE_PAGENUM_PREFIX.match(ln.text):
            continue
        score, sig = score_line(ln, s_body, median_leading, col_width, gap)
        # Guard B: numbering is only a strong signal WITH corroboration —
        # otherwise body lines like "8 conjugados..." leak in.
        num_strong = (
            sig["num"] and sig["short"]
            and (ln.bold or sig["gap"] or sig["size_ratio"] >= 1.1)
        )
        if score >= 0.5 or num_strong:
            candidates.append((ln, score, sig))

    if not candidates:
        return []

    candidates = _merge_wrapped(candidates, median_leading)

    # style clustering -> level rank
    styles = {(round(ln.size), ln.bold) for ln, _, _ in candidates}
    ranked = sorted(styles, key=lambda s: (-s[0], not s[1]))  # size desc, bold first
    style_level = {st: min(i + 1, 4) for i, st in enumerate(ranked)}

    headings: list[Heading] = []
    for i, (ln, score, sig) in enumerate(candidates):
        lvl = style_level[(round(ln.size), ln.bold)]
        # numbering overrides style-derived level with dot depth
        m = RE_DOTNUM.match(ln.text)
        if m:
            depth = m.group(1).count(".") + 1
            lvl = min(depth, 4)
        headings.append(Heading(lvl, ln.text, ln.page, i, round(score, 2), sig))

    return _repair_tree(headings)


def _merge_wrapped(candidates, median_leading):
    """Merge a heading that wrapped onto the next line (same style, tight gap,
    first line not sentence-terminated, second line not itself numbered)."""
    merged: list = []
    for ln, score, sig in candidates:
        if merged:
            pln, pscore, psig = merged[-1]
            same_style = round(pln.size) == round(ln.size) and pln.bold == ln.bold
            tight = 0 <= (ln.y0 - pln.y1) <= 1.4 * median_leading
            continuation = not RE_SENTENCE_END.search(pln.text) and not RE_DOTNUM.match(ln.text)
            if pln.page == ln.page and same_style and tight and continuation:
                pln.text = f"{pln.text} {ln.text}".strip()
                pln.y1 = ln.y1
                merged[-1] = (pln, max(pscore, score), psig)
                continue
        merged.append((ln, score, sig))
    return merged


def _leadings(lines: list[Line]) -> list[float]:
    out = []
    prev = None
    for ln in lines:
        if prev and prev.page == ln.page and ln.y0 >= prev.y1:
            out.append(ln.y0 - prev.y1)
        prev = ln
    return out


def _repair_tree(headings: list[Heading]) -> list[Heading]:
    """Enforce monotonic nesting: never jump a level deeper than parent+1."""
    fixed: list[Heading] = []
    prev_level = 0
    for h in headings:
        if h.level > prev_level + 1:
            h.level = prev_level + 1
        fixed.append(h)
        prev_level = h.level
    return fixed


# --- CLI / inspection ------------------------------------------------------
def run(path: Path, debug: bool) -> None:
    doc = fitz.open(path)
    source = "toc" if (doc.get_toc(simple=True) and
                       len({e[2] for e in doc.get_toc(simple=True)}) > 1) else "heuristic"
    headings = extract_headings(doc, debug=debug)
    print(f"\n{'='*70}\n{path.name}  ({doc.page_count}pp, source={source}, {len(headings)} headings)\n{'='*70}")
    for h in headings[:60]:
        indent = "  " * (h.level - 1)
        conf = "" if source == "toc" else f"  [conf={h.confidence} {_sig(h.signals)}]"
        print(f"{indent}{'#'*h.level} {h.text[:70]}  (p{h.page+1}){conf}" if debug
              else f"{indent}{h.text[:72]}")
    if len(headings) > 60:
        print(f"  ... +{len(headings) - 60} more")


def _sig(s: dict) -> str:
    return " ".join(k for k, v in s.items() if v is True)


def main(argv: list[str]) -> int:
    debug = "--debug" in argv
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        print("usage: headings.py <file-or-dir> [--debug]")
        return 1
    targets: list[Path] = []
    for a in args:
        p = Path(a)
        targets += sorted(p.glob("*.pdf")) if p.is_dir() else [p]
    for t in targets:
        if t.suffix.lower() == ".pdf":
            run(t, debug)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
