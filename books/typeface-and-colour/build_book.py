#!/usr/bin/env python3
"""THE LETTER & THE HUE — a two-chapter visual book on typefaces and colour.

Sixty A4 pages. Chapter I, "The Letter", follows Edward Johnston's doctrine of
lettering — essential forms, the pen, serifs, the nine qualities, proportion,
classification, spacing, and the page. Chapter II, "The Hue", follows M. E.
Chevreul's law of simultaneous contrast and his taxonomy of colour harmonies.

Build:
    uv run --project $FRAMEFORGE_ROOT python build_book.py [--out DIR]
"""
from __future__ import annotations

import argparse
import math
import os
import sys

FRAMEFORGE = os.environ.get(
    "FRAMEFORGE_ROOT", "/home/admin/github-mirror/_framework/frameforge"
)
sys.path[:0] = [FRAMEFORGE, os.path.join(FRAMEFORGE, "src"), os.path.join(FRAMEFORGE, "docs")]
_shadow = sys.modules.get("framegraph")
if _shadow is not None and not hasattr(_shadow, "__path__"):
    del sys.modules["framegraph"]

from framegraph.sdk import DocumentBuilder, serialize, stroke  # noqa: E402
from framegraph.sdk.validate import validate_static_rules  # noqa: E402

# --------------------------------------------------------------------------- #
# Canvas & identity                                                            #
# --------------------------------------------------------------------------- #

W, H = 794, 1123  # A4 portrait @ 96 dpi
CANVAS = {"size": [W, H], "units": "px"}
M = 68            # side margin
TOP = 76          # top of text area
BOT = H - 96      # bottom of text area
CW = W - 2 * M    # column width

PAPER = "#fbf8f1"
INK = "#221d16"
SUB = "#6e6252"
RUBRIC = "#a63a2b"
HAIR = "#ddd3c0"
PANEL = "#f2ecdf"
PANEL2 = "#e9e0cd"
GOLD = "#b98a2e"
NIGHT = "#241f18"
CREAM = "#f4ead6"

SERIF = ["EB Garamond", "Georgia", "serif"]
SERIFB = ["EB Garamond 12", "EB Garamond", "Georgia", "serif"]
SANS = ["Inter", "Arial", "sans-serif"]
MONO = ["Fira Mono", "DejaVu Sans Mono", "monospace"]

# Specimen families (all installed; chosen per historical class)
F_GARALDE = ["EB Garamond", "serif"]
F_DIDONE = ["GFS Didot", "Latin Modern Roman", "serif"]
F_GROT = ["Arimo", "DejaVu Sans", "sans-serif"]
F_NEOGROT = ["Inter", "sans-serif"]
F_HUMSANS = ["Gillius ADF", "Fira Sans", "sans-serif"]
F_GEOSANS = ["Cabin", "Comfortaa", "sans-serif"]
F_MONO = ["Fira Mono", "monospace"]

COLORS = {
    "paper": PAPER, "ink": INK, "sub": SUB, "rubric": RUBRIC, "hair": HAIR,
    "panel": PANEL, "panel2": PANEL2, "gold": GOLD, "night": NIGHT, "cream": CREAM,
}

STYLES = {
    "runhead":  dict(font_family=SANS, font_size=9.5, color="sub",
                     letter_spacing=2.5, text_transform="uppercase"),
    "runheadR": dict(font_family=SANS, font_size=9.5, color="sub",
                     letter_spacing=2.5, text_transform="uppercase", align="right"),
    "folioC":   dict(font_family=MONO, font_size=10, color="sub",
                     letter_spacing=2, align="center"),
    "kicker":   dict(font_family=SANS, font_size=11.5, font_weight=600, color="rubric",
                     letter_spacing=4, text_transform="uppercase"),
    "kickerC":  dict(font_family=SANS, font_size=11.5, font_weight=600, color="rubric",
                     letter_spacing=4, text_transform="uppercase", align="center"),
    "hcover":   dict(font_family=SERIF, font_size=88, color="ink",
                     line_height=1.0, align="center"),
    "h1":       dict(font_family=SERIF, font_size=52, color="ink", line_height=1.05),
    "h1C":      dict(font_family=SERIF, font_size=52, color="ink", line_height=1.05,
                     align="center"),
    "h2":       dict(font_family=SERIF, font_size=29, color="ink", line_height=1.14),
    "h3":       dict(font_family=SERIF, font_size=19, color="ink", line_height=1.25),
    "h3r":      dict(font_family=SERIF, font_size=19, color="rubric", line_height=1.25),
    "lead":     dict(font_family=SERIF, font_size=18.5, color="ink", italic=True,
                     line_height=1.5),
    "leadC":    dict(font_family=SERIF, font_size=18.5, color="sub", italic=True,
                     line_height=1.5, align="center"),
    "body":     dict(font_family=SERIF, font_size=14.5, color="ink", line_height=1.6),
    "bodyS":    dict(font_family=SERIF, font_size=13, color="sub", line_height=1.55),
    "cap":      dict(font_family=SANS, font_size=10.5, color="sub", line_height=1.5),
    "capC":     dict(font_family=SANS, font_size=10, color="sub", line_height=1.5,
                     align="center"),
    "label":    dict(font_family=SANS, font_size=10, font_weight=600, color="ink",
                     letter_spacing=1.2),
    "labelC":   dict(font_family=SANS, font_size=10, font_weight=600, color="ink",
                     letter_spacing=1.2, align="center"),
    "labelR":   dict(font_family=SANS, font_size=10, font_weight=600, color="rubric",
                     letter_spacing=2, text_transform="uppercase"),
    "labelRC":  dict(font_family=SANS, font_size=10, font_weight=600, color="rubric",
                     letter_spacing=2, text_transform="uppercase", align="center"),
    "quote":    dict(font_family=SERIF, font_size=24, color="ink", italic=True,
                     line_height=1.45),
    "quoteC":   dict(font_family=SERIF, font_size=24, color="ink", italic=True,
                     line_height=1.45, align="center"),
    "attr":     dict(font_family=SANS, font_size=10.5, color="sub", letter_spacing=1.5,
                     text_transform="uppercase"),
    "attrC":    dict(font_family=SANS, font_size=10.5, color="sub", letter_spacing=1.5,
                     text_transform="uppercase", align="center"),
    "mono":     dict(font_family=MONO, font_size=11.5, color="ink", line_height=1.5),
    "cnum":     dict(font_family=SERIF, font_size=150, color="rubric", line_height=1.0),
    "toc":      dict(font_family=SERIF, font_size=15.5, color="ink", line_height=1.5),
    "tocn":     dict(font_family=MONO, font_size=12, color="sub", align="right"),
}

BOOK_TITLE = "THE LETTER & THE HUE"


def mix(c1: str, c2: str, t: float) -> str:
    """Linear blend of two hex colours; t=0 -> c1, t=1 -> c2."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def tone(hue_hex: str, t: float) -> str:
    """Chevreul tone: t in [-1, 1]; negative lowers with white, positive deepens with black."""
    return mix(hue_hex, "#ffffff", -t) if t < 0 else mix(hue_hex, "#100c08", t)


# The twelve-hue painter's wheel (red-yellow-blue primaries, as Chevreul used).
WHEEL = [
    ("Red", "#c22f2a"), ("Red-orange", "#d0512b"), ("Orange", "#dd7327"),
    ("Yellow-orange", "#e29a25"), ("Yellow", "#e2bd2a"), ("Yellow-green", "#9aa62f"),
    ("Green", "#4c8b46"), ("Blue-green", "#2e7f74"), ("Blue", "#31618f"),
    ("Blue-violet", "#4b4a86"), ("Violet", "#6b4177"), ("Red-violet", "#96355b"),
]

# --------------------------------------------------------------------------- #
# Page chrome & shared vocabulary                                              #
# --------------------------------------------------------------------------- #

_page_no = 0

# Inline text fields the model wants folded into a `style` dict.
_STYLE_FIELDS = {
    "font_family", "font_size", "font_weight", "color", "italic", "align",
    "letter_spacing", "line_height", "text_transform", "decoration",
}


def _styled_text(p):
    orig = p.text

    def text(box, txt, **fields):
        style = fields.pop("style", None)
        sf = {k: fields.pop(k) for k in list(fields) if k in _STYLE_FIELDS}
        if sf:
            if "decoration" in sf:
                sf["text_decoration"] = sf.pop("decoration")
            if isinstance(style, str):
                sf = {"class": style, **sf}
            elif isinstance(style, dict):
                sf = {**sf, **style}
            style = sf
        if style is not None:
            return orig(box, txt, style=style, **fields)
        return orig(box, txt, **fields)

    p.text = text
    return p


def new_page(b, pid, *, chapter="", chrome=True, bg="paper", folio=True):
    global _page_no
    _page_no += 1
    p = _styled_text(b.page(pid, canvas=CANVAS, coordinate_mode="absolute"))
    p.layer("bg")
    p.rect([0, 0, W, H], fill=bg)
    p.layer("main")
    if chrome:
        p.text([M, 38, 340, 13], BOOK_TITLE, style="runhead")
        if chapter:
            p.text([W - M - 360, 38, 360, 13], chapter, style="runheadR")
        p.rect([M, 56, CW, 1], fill="hair")
        p.rect([M, H - 62, CW, 1], fill="hair")
    if folio:
        p.text([0, H - 50, W, 14], str(_page_no), style="folioC")
    return p


def section_head(p, y, kicker, title, *, lead=None):
    """Kicker + heading + optional lead; returns the y below the block."""
    p.text([M, y, CW, 16], kicker, style="kicker")
    p.text([M, y + 26, CW, 74], title, style="h2")
    yy = y + 26 + 44
    if lead:
        p.text([M, yy + 8, CW, 60], lead, style="lead")
        yy += 76
    p.rect([M, yy + 6, 64, 2], fill="rubric")
    return yy + 30


_plate_no = 0
PLATES: list[tuple[int, str, int]] = []  # (plate no, short title, page no)


def _short_title(txt: str, limit: int = 46) -> str:
    """A deliberate short title: first clause, cut at a word boundary."""
    s = txt.replace("“", "").replace("”", "")
    s = s.split(".")[0].split(" — ")[0].split(";")[0].split(",")[0].split(":")[0]
    if len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0]
    s = s.rstrip(" :,–—…")
    if "(" in s and ")" not in s:
        s = s.split("(")[0].rstrip()
    stop = {"and", "of", "the", "a", "an", "for", "with", "in", "on", "by", "as"}
    while s.split() and s.split()[-1].lower() in stop:
        s = s.rsplit(" ", 1)[0]
    return s.strip()


def caption(p, box, txt):
    global _plate_no
    _plate_no += 1
    x, y, w, h = box
    p.text([x, y, 76, 13], f"PLATE {_plate_no}", style="labelR")
    p.text([x + 84, y, w - 84, h], txt, style="cap")
    PLATES.append((_plate_no, _short_title(txt), _page_no))


QUOTES: list[tuple[str, float, float, float, float]] = []


def display_quote(p, box, text, *, size, line_height=1.45, color=None, align=None):
    """A must-not-clip quotation: registered and gated at build time."""
    QUOTES.append((text, box[2], box[3], size, line_height))
    st = {"class": "quote", "font_size": size, "line_height": line_height}
    if color:
        st["color"] = color
    if align:
        st["align"] = align
    p.text(box, "“" + text + "”", style=st)


NOTES: list[tuple[str, float, float, float]] = []


def note(p, box, txt, *, size=10.5):
    """A must-not-clip narrative note (cap style): registered and gated."""
    NOTES.append((txt, box[2], box[3], size))
    p.text(box, txt, style={"class": "cap", "font_size": size})


def check_notes() -> list[str]:
    problems = []
    for text, w, h, size in NOTES:
        cpl = max(1, int(w / (0.47 * size)))
        lines = -(-len(text) // cpl)
        needed = lines * size * 1.5
        if needed > h:
            problems.append(f"note clips: needs ~{needed:.0f}px, box {h}px: "
                            f"{text[:60]}…")
    return problems


def check_quotes() -> list[str]:
    """Estimate wrapped height for every display quote; flag any that would clip.

    Conservative estimate: average glyph advance ~0.5 em for Garamond italic.
    """
    problems = []
    for text, w, h, size, lh in QUOTES:
        cpl = max(1, int(w / (0.50 * size)))
        lines = -(-len(text) // cpl)
        needed = lines * size * lh
        if needed > h:
            problems.append(f"quote clips: needs ~{needed:.0f}px, box {h}px: "
                            f"{text[:60]}…")
    return problems


def quote_block(p, y, text, attr, *, size=24, w=CW, x=M, centered=False):
    st = "quoteC" if centered else "quote"
    at = "attrC" if centered else "attr"
    p.text([x, y, w, 200], "“" + text + "”", style=st)


def framed_panel(p, box, fill="panel"):
    x, y, w, h = box
    p.rect([x, y, w, h], fill=fill)
    p.rect([x, y, w, h], fill="none", **stroke(1, color=HAIR))


# --------------------------------------------------------------------------- #
# Skeleton letters — drawn essential forms                                     #
# --------------------------------------------------------------------------- #
# Each glyph: (advance width, [strokes]) in a unit square; y=0 cap-line,
# y=1 baseline. Strokes: ("l", x1,y1, x2,y2) | ("c", cx,cy, r) | ("a", cx,cy, r, a0,a1).

SKEL = {
    "O": (1.00, [("c", .50, .50, .50)]),
    "H": (0.75, [("l", 0, 0, 0, 1), ("l", .75, 0, .75, 1), ("l", 0, .5, .75, .5)]),
    "A": (0.80, [("l", 0, 1, .40, 0), ("l", .40, 0, .80, 1), ("l", .155, .64, .645, .64)]),
    "E": (0.55, [("l", 0, 0, 0, 1), ("l", 0, 0, .55, 0), ("l", 0, .5, .50, .5),
                  ("l", 0, 1, .55, 1)]),
    "M": (0.92, [("l", 0, 1, .10, 0), ("l", .10, 0, .46, 1), ("l", .46, 1, .82, 0),
                  ("l", .82, 0, .92, 1)]),
    "N": (0.76, [("l", 0, 1, 0, 0), ("l", 0, 0, .76, 1), ("l", .76, 1, .76, 0)]),
    "R": (0.72, [("l", 0, 0, 0, 1), ("l", 0, 0, .40, 0), ("a", .40, .26, .26, -90, 90),
                  ("l", .40, .52, 0, .52), ("l", .38, .52, .72, 1)]),
    "S": (0.60, [("a", .30, .27, .27, 90, 330), ("a", .30, .73, .27, -90, 150)]),
    "T": (0.70, [("l", 0, 0, .70, 0), ("l", .35, 0, .35, 1)]),
    "P": (0.66, [("l", 0, 0, 0, 1), ("l", 0, 0, .38, 0), ("a", .38, .27, .27, -90, 90),
                  ("l", .38, .54, 0, .54)]),
    "I": (0.10, [("l", .05, 0, .05, 1)]),
    "B": (0.62, [("l", 0, 0, 0, 1), ("l", 0, 0, .34, 0), ("a", .34, .24, .24, -90, 90),
                  ("l", .34, .48, 0, .48), ("a", .34, .74, .26, -90, 90),
                  ("l", .34, 1, 0, 1)]),
    "C": (0.86, [("a", .48, .50, .48, 40, 320)]),
    "G": (0.90, [("a", .48, .50, .48, 30, 320), ("l", .96, .55, .60, .55)]),
    "L": (0.55, [("l", 0, 0, 0, 1), ("l", 0, 1, .55, 1)]),
    "V": (0.80, [("l", 0, 0, .40, 1), ("l", .40, 1, .80, 0)]),
    "W": (1.10, [("l", 0, 0, .28, 1), ("l", .28, 1, .55, .12), ("l", .55, .12, .82, 1),
                  ("l", .82, 1, 1.10, 0)]),
    "X": (0.72, [("l", 0, 0, .72, 1), ("l", .72, 0, 0, 1)]),
    "D": (0.80, [("l", 0, 0, 0, 1), ("l", 0, 0, .30, 0), ("a", .30, .50, .50, -90, 90),
                  ("l", .30, 1, 0, 1)]),
    # lowercase (x-height .64; ascender to -0.36 above; descender to 1.32)
    "o": (0.64, [("c", .32, .68, .32)]),
    "n": (0.60, [("l", 0, .36, 0, 1), ("a", .30, .66, .30, 180, 360),
                  ("l", .60, .66, .60, 1)]),
    "e": (0.62, [("a", .31, .68, .31, 22, 335), ("l", .012, .60, .615, .60)]),
    "g": (0.62, [("c", .29, .68, .29), ("l", .58, .36, .58, 1.06),
                  ("a", .30, 1.06, .28, 0, 160)]),
    "l": (0.10, [("l", .05, -.36, .05, 1)]),
    "t": (0.44, [("l", .18, -.20, .18, .92), ("a", .34, .76, .16, 0, 90),
                  ("l", 0, .38, .42, .38)]),
}


def draw_skeleton(p, ch, x, y, s, *, w=3.2, color=INK):
    """Draw the essential (skeleton) form of *ch* with cap-height *s* at (x, y=cap-line)."""
    adv, strokes = SKEL[ch]
    for st in strokes:
        if st[0] == "l":
            p.line([x + st[1] * s, y + st[2] * s], [x + st[3] * s, y + st[4] * s],
                   **stroke(w, color=color, cap="round"))
        elif st[0] == "c":
            p.circle([x + st[1] * s, y + st[2] * s], st[3] * s, fill="none",
                     **stroke(w, color=color))
        elif st[0] == "a":
            p.arc([x + st[1] * s, y + st[2] * s], st[3] * s, st[4], st[5],
                  fill="none", **stroke(w, color=color, cap="round"))
    return adv * s


def skeleton_word(p, word, x, y, s, *, gap=0.22, w=3.2, color=INK):
    cx = x
    for ch in word:
        if ch == " ":
            cx += 0.5 * s
            continue
        cx += draw_skeleton(p, ch, cx, y, s, w=w, color=color) + gap * s
    return cx


# --------------------------------------------------------------------------- #
# FRONT MATTER (pp. 1-6)                                                       #
# --------------------------------------------------------------------------- #

def draw_motif(p, cx, cy, r, *, bg="night"):
    """The book's mark: the wheel of hues held in the skeleton of an O."""
    for i, (_, hx) in enumerate(WHEEL):
        p.sector([cx, cy], r, i * 30 - 90, (i + 1) * 30 - 90, fill=hx)
    p.circle([cx, cy], r * 0.62, fill=bg)
    p.circle([cx, cy], r + max(8, r * 0.06), fill="none",
             **stroke(max(2.5, r * 0.022), color=CREAM))
    p.circle([cx, cy], r * 0.62 - max(6, r * 0.05), fill="none",
             **stroke(max(1.6, r * 0.011), color=CREAM))


def pg_half_title(b):
    p = new_page(b, "cover", chrome=False, folio=False, bg="night")
    p.text([0, 170, W, 20], "FOUR CHAPTERS ON THE CRAFTS OF DESIGN",
           style="kickerC")
    draw_motif(p, W / 2, 440, 168)
    p.text([0, 682, W, 88], "The Letter",
           style={"class": "hcover", "font_size": 72, "color": "#f4ead6"})
    p.text([0, 772, W, 88], "& the Hue",
           style={"class": "hcover", "font_size": 72, "color": "#f4ead6"})
    p.rect([W / 2 - 30, 890, 60, 2], fill="rubric")
    p.text([0, 928, W, 16], "AFTER EDWARD JOHNSTON & M. E. CHEVREUL", style="attrC")


def pg_frontispiece(b):
    p = new_page(b, "frontispiece", chrome=False, folio=False, bg="night")
    cx, cy, r = W / 2, H / 2 - 40, 218
    # A ring of the twelve hues, held inside the drawn skeleton of an O —
    # the two subjects of this book in one figure.
    draw_motif(p, cx, cy, r)
    p.text([0, cy + r + 74, W, 20], "EVERY LETTER IS A FORM · EVERY FORM MEETS A GROUND",
           style="labelRC")
    p.text([0, cy + r + 104, W, 40],
           "Frontispiece — the wheel of hues set within the skeleton of an O.",
           style="capC")


def pg_title(b):
    p = new_page(b, "title", chrome=False, folio=False)
    p.text([0, 240, W, 20], "FOUR CHAPTERS ON THE CRAFTS OF DESIGN", style="kickerC")
    p.text([0, 300, W, 110], "The Letter", style="hcover")
    p.text([0, 412, W, 110], "& the Hue", style="hcover")
    p.rect([W / 2 - 40, 540, 80, 2.5], fill="rubric")
    for i, ln in enumerate([
            "Chapter I · The Letter — on the forms of typefaces",
            "Chapter II · The Hue — on the behaviour of colour",
            "Chapter III · The Arrangement — on balance, rhythm & harmony",
            "Chapter IV · Colour at Work — on the law applied",
    ]):
        p.text([0, 570 + i * 32, W, 30], ln, style={"class": "leadC", "font_size": 16.5})
    # small skeleton flourish
    skeleton_word(p, "AHO", W / 2 - 86, 720, 52, w=3, color=RUBRIC)
    p.text([0, 950, W, 16], "AFTER EDWARD JOHNSTON & M. E. CHEVREUL", style="attrC")
    p.text([0, 976, W, 16], "MMXXVI", style="attrC")


def pg_copyright(b):
    p = new_page(b, "copyright", chrome=False, folio=False)
    y = 640
    p.text([M, y, CW, 60],
           "This book is a study, in words and drawn plates, of two old crafts: "
           "the design of letters and the ordering of colour.", style="bodyS")
    p.text([M, y + 70, CW, 130],
           "Chapter I is built on Edward Johnston's Writing & Illuminating, & Lettering "
           "(London, 1906), the foundation text of modern lettering education. "
           "Chapter II is built on M. E. Chevreul's law of the simultaneous contrast of "
           "colours (Paris, 1839, in its English translations). Direct quotations are "
           "attributed in place; everything else is the author's reading of those works.",
           style="bodyS")
    p.text([M, y + 214, CW, 40],
           "Quotations are verbatim from the editions named on the Sources page; "
           "omissions are marked by ellipses and editorial insertions by brackets. "
           "Where nineteenth-century spelling differs from modern use, the original "
           "is kept. Interpretations, orderings and all drawn plates are the "
           "author's: every plate is an original drawing — either a demonstration "
           "devised for this book or a reconstruction after its named source.",
           style="cap")
    note(p, [M, y + 292, CW, 52],
         "Written as a visual primer for working designers — neither a history "
         "nor a complete manual. Set in EB Garamond, with Inter for annotation "
         "and Fira Mono for figures; exact designers and licences for every "
         "specimen face are tabled with the List of Plates.")
    note(p, [M, y + 352, CW, 20],
         "All plates are drawn, not photographed. Composed by Claude (Fable 5).")
    note(p, [M, y + 376, CW, 18],
         "Second edition — four chapters (build 2026-07-02) — July 2026.")


def pg_contents(b):
    p = new_page(b, "contents", chrome=False, folio=True)
    p.text([M, 96, CW, 20], "CONTENTS", style="kicker")
    p.text([M, 134, CW, 60], "Contents", style="h1")
    col1 = [
        ("Preface — the four crafts", 6, False),
        ("CHAPTER I · THE LETTER", 7, True),
        ("1.1   A lineage of two thousand years", 8, False),
        ("1.2   The essential form", 10, False),
        ("1.3   The tool makes the letter", 12, False),
        ("1.4   The serif — a stroke, finished", 14, False),
        ("1.5   Anatomy of the letterform", 16, False),
        ("1.6   The nine qualities", 18, False),
        ("1.7   The widths of the capitals", 23, False),
        ("1.8   A taxonomy of typefaces", 25, False),
        ("1.9   Weight — the family", 31, False),
        ("1.10  Scale — a modular scale", 32, False),
        ("1.11  Spacing", 33, False),
        ("1.12  Measure & leading", 34, False),
        ("1.13  The page — margins", 35, False),
        ("CHAPTER II · THE HUE", 37, True),
        ("2.1   Tones, scales & hues", 38, False),
        ("2.2   The wheel", 40, False),
        ("2.3   Simultaneous contrast", 41, False),
        ("2.4   Successive contrast", 43, False),
        ("2.5   The complementaries", 44, False),
        ("2.6   The six harmonies", 45, False),
        ("2.7   Colours with the neutrals", 52, False),
        ("2.8   Value does the work", 53, False),
        ("2.9   Colour meets the letter", 54, False),
        ("2.10  Red, the working accent", 55, False),
        ("2.11  The closed palette", 56, False),
        ("2.12  Never hue alone", 57, False),
    ]
    col2 = [
        ("CHAPTER III · THE ARRANGEMENT", 59, True),
        ("3.1   The third craft", 60, False),
        ("3.2   Terms & principles", 61, False),
        ("3.3   Tone balance", 62, False),
        ("3.4   Measure balance — the steelyard", 64, False),
        ("3.5   Shape balance & symmetry", 66, False),
        ("3.6   Rhythm", 68, False),
        ("3.7   Harmony — likeness in accord", 70, False),
        ("3.8   The page as pure design", 72, False),
        ("3.9   The grid — a balance instrument", 73, False),
        ("CHAPTER IV · COLOUR AT WORK", 75, True),
        ("4.1   The Gobelins complaint", 76, False),
        ("4.2   The room", 78, False),
        ("4.3   The garden", 80, False),
        ("4.4   The person", 82, False),
        ("4.5   The printed page", 84, False),
        ("4.6   1839, re-measured", 86, False),
        ("4.7   The designer as dyer", 87, False),
        ("END MATTER", 90, True),
        ("Sources & further reading", 90, False),
        ("List of plates", 91, False),
        ("Specimen sources", 92, False),
    ]
    colw = (CW - 28) / 2
    for ci, entries in enumerate((col1, col2)):
        x = M + ci * (colw + 28)
        y = 224
        for label, page_n, is_ch in entries:
            if is_ch:
                y += 12
                p.text([x, y, colw - 50, 20], label,
                       style={"class": "h3r", "font_size": 15.5})
                p.text([x + colw - 44, y + 2, 44, 14], str(page_n), style="tocn")
                y += 32
                continue
            p.text([x + 12, y, colw - 68, 18], label,
                   style={"class": "toc", "font_size": 12.5})
            p.text([x + colw - 44, y + 1, 44, 14], str(page_n), style="tocn")
            y += 23


def pg_preface(b):
    p = new_page(b, "preface", chrome=False, folio=True)
    p.text([M, 108, CW, 20], "PREFACE", style="kicker")
    p.text([M, 146, CW, 60], "The four crafts", style="h1")
    p.rect([M, 222, 64, 2], fill="rubric")
    p.text([M, 252, CW, 156],
           "A page works on the reader more than once. Through its letters — the "
           "shapes that carry the words; through its colour — the ground, the ink, "
           "whatever third voice the designer admits; and through its arrangement — "
           "the quiet mechanics of where everything sits. These crafts are usually "
           "taught apart. This book holds them together.",
           style="lead")
    p.text([M, 424, CW, 168],
           "Chapter I is about the letter. Its guide is Edward Johnston, the scribe "
           "who, in 1906, reduced good lettering to a short table of virtues — "
           "readableness first, then fitness for use, then beauty and character — and "
           "showed that every typeface is the record of a tool moving over a surface. "
           "Chapter II is about colour, and its guide is Michel-Eugène Chevreul, the "
           "chemist of the Gobelins dye-works, who found that colours change one "
           "another merely by standing side by side, and derived from that single law "
           "a complete grammar of harmony and contrast.",
           style="body")
    p.text([M, 608, CW, 168],
           "Chapter III is about arrangement, and its guide is Ernest Batchelder, who "
           "carried Denman Ross's Harvard theory of pure design to working craftsmen "
           "through the pages of a printers' trade journal: composition reduced to "
           "three terms — tones, measures, shapes — governed by three principles — "
           "rhythm, balance, harmony. Chapter IV returns to Chevreul and follows the "
           "law out of the laboratory, into the room, the garden, the wardrobe and "
           "the printing house — and re-measures his oldest experiment with modern "
           "instruments.",
           style="body")
    p.text([M, 800, CW, 130],
           "The first two chapters teach the materials; the last two teach their "
           "government and their use. Read the plates as slowly as the prose. They "
           "are drawn, not decorated: each one exists to make a claim visible, and "
           "several exist to test one.",
           style="body")


# --------------------------------------------------------------------------- #
# CHAPTER I — THE LETTER (pp. 7-36)                                            #
# --------------------------------------------------------------------------- #

CH1 = "Chapter I · The Letter"


def pg_ch1_opener(b):
    p = new_page(b, "ch1-opener", chrome=False, folio=False, bg="ink")
    p.text([M, 200, CW, 24], "CHAPTER I", style="kickerC")
    p.text([0, 240, W, 170], "I", style={"class": "cnum", "align": "center"})
    p.text([0, 430, W, 80], "The Letter",
           style={"class": "hcover", "color": "#f4ead6"})
    p.rect([W / 2 - 30, 560, 60, 2], fill="rubric")
    p.text([120, 610, W - 240, 90],
           "On the essential forms of letters, the tools that shaped them, the "
           "qualities that make lettering good, and the families of typefaces "
           "that descend from a Roman wall.",
           style={"class": "leadC", "color": "#d8c9ae"})
    # skeleton alphabet strip
    skeleton_word(p, "AEHMORS", 148, 800, 56, w=3, color=CREAM)


def pg_lineage_text(b):
    p = new_page(b, "lineage-1", chapter=CH1)
    y = section_head(p, 96, "1.1 · LINEAGE", "A lineage of two thousand years",
                     lead="The roman letters on our screens descend, by a line of "
                          "copies two thousand years long, from capitals cut in "
                          "stone under Trajan.")
    p.text([M, y, CW, 168],
           "Within the Latin tradition, the Roman capital reached its monumental form "
           "about two thousand years ago and has never been displaced. Johnston, "
           "writing in 1906, gave the reason "
           "plainly: since their full development, “the Roman Capitals have held the "
           "supreme place among letters for readableness and beauty.” They are the "
           "best forms for the grandest inscriptions, and the penman is told to study a "
           "fine monumental model — the Trajan Column inscription above all — and embody "
           "its virtues in his own hand.",
           style="body")
    p.text([M, y + 190, CW, 168],
           "What followed was not replacement but adaptation. Scribes writing quickly "
           "rounded the capitals into uncials; smaller, faster hands became half-uncials; "
           "Charlemagne's schools regularised the minuscule; and when the first Italian "
           "printers needed models for their type, they did what designers still do — "
           "they went back. “The Italian type-founders of the fifteenth century "
           "sought out fine examples of old writing as models, and for their capitals "
           "studied the monumental Roman inscriptions.” Our lowercase is formalised "
           "handwriting; our capitals are architecture.",
           style="body")
    p.text([M, y + 400, CW, 100],
           "The lineage matters practically, not sentimentally. It explains why letters "
           "have the parts they have — why strokes swell and taper, why serifs finish a "
           "stem, why an italic slopes. Each is the fossil of a tool.",
           style="body")
    display_quote(p, [M, BOT - 122, CW, 88],
                  "We have an excellent precedent in the Italian scribes who went "
                  "back 300 years for a model and gave us the Roman small-letter "
                  "as a result.", size=19, line_height=1.45)
    p.text([M, BOT - 22, CW, 14],
           "EDWARD JOHNSTON, WRITING & ILLUMINATING, & LETTERING (1906), PREFACE",
           style="attr")


def pg_lineage_plate(b):
    p = new_page(b, "lineage-2", chapter=CH1)
    framed_panel(p, [M, 90, CW, 880])
    x0 = M + 96
    y0, y1 = 140, 900
    p.line([x0, y0], [x0, y1], **stroke(2, color=SUB))
    events = [
        (114, "ROMAN CAPITALS", "The monumental square capital; the Trajan Column, c. 113",
         "TRAIAN", 30, True),
        (350, "UNCIALS", "Pen-rounded capitals of the book hands, c. 300–700",
         "De", 34, False),
        (600, "HALF-UNCIALS", "The pen invents ascenders & descenders — true small letters",
         "min", 34, False),
        (800, "CAROLINGIAN MINUSCULE", "Charlemagne's schools regularise the book hand, c. 800",
         "lege", 34, False),
        (1200, "GOTHIC / BLACK-LETTER", "The hand compresses; the page darkens, c. 1150–1500",
         "text", 34, False),
        (1430, "HUMANIST ROMAN TYPE", "Italian founders cut type on the old models — Jenson, Aldus (1465–95)",
         "Roma", 34, False),
        (1540, "THE ITALIC", "Aldus prints compact cursive type (1501): a second voice for the roman",
         "italic", 34, False),
        (1957, "THE NEO-GROTESQUE", "Helvetica & Univers, 1957 — the skeleton letter, rationalised",
         "Neu", 34, False),
    ]
    t0, t1 = 100, 2000
    for yr, label, note, sample, size, caps in events:
        ty = y0 + (yr - t0) / (t1 - t0) * (y1 - y0)
        p.circle([x0, ty], 5, fill=RUBRIC)
        shown = {1430: "c. 1465", 1540: "1501"}.get(
            yr, ("c. " + str(yr)) if yr < 1430 else str(yr))
        p.text([x0 - 84, ty - 7, 66, 14], shown, style="tocn")
        p.text([x0 + 26, ty - 22, 300, 14], label, style="labelR")
        p.text([x0 + 26, ty - 2, CW - 200, 34], note, style="cap")
        p.text([x0 + 372, ty - 30, 240, 60], sample,
               font_family=SERIF, font_size=size, color=INK,
               italic=(label == "THE ITALIC"))
    caption(p, [M, 996, CW, 40],
            "The descent of the letter. Every entry is a change of tool or of hand; "
            "none is a change of the underlying skeleton.")


def pg_essential_text(b):
    p = new_page(b, "essential-1", chapter=CH1)
    y = section_head(p, 96, "1.2 · ESSENTIAL FORM", "The skeleton beneath the letter",
                     lead="Strip a letter of every finish the tool gave it, and what "
                          "remains is its essential form.")
    p.text([M, y, CW, 150],
           "Johnston defined the essential forms as “the necessary parts” — the "
           "structural plan of an alphabet. Draw the Roman capitals in single pencil "
           "strokes, as a child does learning its letters, and you have them: a circle "
           "for O, two slopes and a bar for A, posts and a lintel for H. These skeletons "
           "are not any one typeface. They are what every typeface must contain to remain "
           "that letter.",
           style="body")
    p.text([M, y + 172, CW, 128],
           "The claim beneath this is quietly radical: the essential form of a letter is "
           "“a purely abstract form, the common property of every rational age and "
           "country,” and its characterisation — its weight, contrast, serifs, its "
           "voice — “is mainly the product of tools and materials.” Design, in "
           "other words, is what happens between the skeleton and the surface.",
           style="body")
    # small demonstration row: skeleton -> seriffed type
    yy = y + 330
    framed_panel(p, [M, yy, CW, 210])
    skeleton_word(p, "R", M + 70, yy + 50, 110, w=4)
    p.text([M + 250, yy + 30, 160, 150], "R", font_family=SERIF, font_size=150,
           color=INK)
    p.text([M + 420, yy + 30, 160, 150], "R", font_family=F_DIDONE, font_size=150,
           color=INK)
    p.text([M + 70, yy + 178, 160, 13], "THE SKELETON", style="labelR")
    p.text([M + 250, yy + 178, 160, 13], "A GARALDE R", style="label")
    p.text([M + 420, yy + 178, 200, 13], "A DIDONE R", style="label")
    caption(p, [M, yy + 236, CW, 40],
            "One essential form, three characterisations. The skeleton is common "
            "property; everything else is the tool speaking.")
    display_quote(p, [M, BOT - 118, CW, 84],
                  "One of the finest things the letter-craftsman can do, is to "
                  "make the Essential Forms of letters beautiful in themselves.",
                  size=19, line_height=1.45)
    p.text([M, BOT - 22, CW, 14], "JOHNSTON, ON SIMPLICITY — CH. XIV", style="attr")


def pg_skeleton_plate(b):
    p = new_page(b, "essential-2", chapter=CH1)
    framed_panel(p, [M, 90, CW, 620])
    # guide lines
    rows = [(170, 74), (330, 74), (490, 74)]
    letters = [["A", "E", "H", "M", "N", "O"], ["R", "S", "T", "B", "C", "D"],
               ["G", "L", "P", "V", "X", "W"]]
    for (ry, s), row in zip(rows, letters):
        p.line([M + 30, ry], [M + CW - 30, ry], **stroke(1, color=HAIR))
        p.line([M + 30, ry + s], [M + CW - 30, ry + s], **stroke(1, color=HAIR))
        x = M + 44
        for ch in row:
            adv = draw_skeleton(p, ch, x, ry, s, w=3.4)
            x += adv + 30
    # lowercase row with x-height guides
    ry, s = 620, 60
    xh = 0.36 * s
    p.line([M + 30, ry], [M + CW - 30, ry], **stroke(1, color=HAIR))
    p.line([M + 30, ry + xh], [M + CW - 30, ry + xh], **stroke(1, color=RUBRIC))
    p.line([M + 30, ry + s], [M + CW - 30, ry + s], **stroke(1, color=HAIR))
    x = M + 44
    for ch in "onelgt":
        adv = draw_skeleton(p, ch, x, ry, s, w=3.2)
        x += adv + 26
    p.text([M + CW - 150, ry + xh - 16, 140, 12], "X-HEIGHT LINE", style="labelR")
    caption(p, [M, 736, CW, 40],
            "The essential forms of the capitals and of six small letters, drawn as "
            "single strokes. No serif, no contrast, no style — and yet every letter "
            "is unmistakably itself.")
    p.text([M, 812, CW, 130],
           "Two things are worth noticing. First, the capitals do not share one width: "
           "O and C claim a full square, E and S about half of one, M and W more than "
           "all of it. Proportion is part of the skeleton, not part of the styling. "
           "Second, the small letters are not shrunken capitals but a different system — "
           "arches, bowls and stems built for the speed of a writing hand.",
           style="body")


def pg_tool_text(b):
    p = new_page(b, "tool-1", chapter=CH1)
    y = section_head(p, 96, "1.3 · THE TOOL", "The tool makes the letter",
                     lead="Hold a broad pen at a constant angle and the alphabet's "
                          "thicks and thins appear of their own accord.")
    p.text([M, y, CW, 150],
           "Take the skeleton letters and write them with a square-cut pen — the reed or "
           "quill of the scribes. The pen is broad in one direction and thin in the "
           "other, so it gives “a thin horizontal stroke and a thick vertical "
           "stroke” without any decision by the writer. The moment the skeleton "
           "passes through that tool, the letter acquires modulation: the alternation of "
           "thick and thin that we now read as ‘serif type’.",
           style="body")
    p.text([M, y + 172, CW, 128],
           "Johnston's demonstration continues: slant the pen and the distribution of "
           "weight rotates with it — oblique strokes lighten, the verticals of M and N "
           "thin, curves gain a diagonal axis. Nearly every historical difference "
           "between one class of typeface and the next can be read as a change in this "
           "single variable: the angle, breadth and freedom of the tool.",
           style="body")
    p.text([M, y + 320, CW, 100],
           "The plate opposite performs the experiment. The same skeleton O and I are "
           "passed through three tools: a pencil point, a straight pen, a slanted pen. "
           "Nothing else changes — and three eras of type fall out.",
           style="body")


def pg_tool_plate(b):
    p = new_page(b, "tool-2", chapter=CH1)
    framed_panel(p, [M, 90, CW, 850])
    colw = CW / 3
    heads = ["THE POINT", "THE STRAIGHT PEN", "THE SLANTED PEN"]
    notes = ["The skeleton: line without weight.",
             "Thick verticals, thin horizontals;\nvertical axis in the curves.",
             "Weight rotates ~30°; oblique axis;\nthe old-style letter."]
    for i, (hd, nt) in enumerate(zip(heads, notes)):
        cx = M + i * colw + colw / 2
        p.text([M + i * colw, 130, colw, 14], hd, style="labelRC")
        p.text([M + i * colw + 12, 156, colw - 24, 40], nt, style="capC")
    # Row 1: the O three ways
    ry, r = 330, 92
    p.circle([M + colw * 0.5, ry], r, fill="none", **stroke(3, color=INK))
    # straight-pen O: two arcs thick left/right — emulate with rings clipped: draw as two arcs
    p.circle([M + colw * 1.5, ry], r, fill="none", **stroke(3, color=INK))
    p.arc([M + colw * 1.5, ry], r, 115, 245, fill="none", **stroke(17, color=INK))
    p.arc([M + colw * 1.5, ry], r, -65, 65, fill="none", **stroke(17, color=INK))
    p.circle([M + colw * 2.5, ry], r, fill="none", **stroke(3, color=INK))
    p.arc([M + colw * 2.5, ry], r, 135, 265, fill="none", **stroke(17, color=INK))
    p.arc([M + colw * 2.5, ry], r, -45, 85, fill="none", **stroke(17, color=INK))
    # Row 2: the I three ways (stem + heads)
    iy0, iy1 = 520, 760
    p.line([M + colw * 0.5, iy0], [M + colw * 0.5, iy1], **stroke(3.4, color=INK, cap="round"))
    p.rect([M + colw * 1.5 - 9, iy0, 18, iy1 - iy0], fill=INK)
    p.line([M + colw * 1.5 - 26, iy0], [M + colw * 1.5 + 26, iy0], **stroke(4, color=INK))
    p.line([M + colw * 1.5 - 26, iy1], [M + colw * 1.5 + 26, iy1], **stroke(4, color=INK))
    # slanted-pen I: sheared stem
    p.polygon([[M + colw * 2.5 - 9, iy0 + 6], [M + colw * 2.5 + 9, iy0],
               [M + colw * 2.5 + 9, iy1 - 6], [M + colw * 2.5 - 9, iy1]], fill=INK)
    p.line([M + colw * 2.5 - 30, iy0 + 8], [M + colw * 2.5 + 22, iy0 - 8],
           **stroke(5, color=INK, cap="round"))
    p.line([M + colw * 2.5 - 22, iy1 + 8], [M + colw * 2.5 + 30, iy1 - 8],
           **stroke(5, color=INK, cap="round"))
    # pen-angle indicators
    for i, ang in [(1, 0), (2, 30)]:
        cx = M + i * colw + colw / 2
        ax, ay = cx, 850
        p.circle([ax, ay], 26, fill="none", **stroke(1, color=SUB))
        rad = math.radians(ang)
        p.line([ax - 24 * math.cos(rad), ay + 24 * math.sin(rad)],
               [ax + 24 * math.cos(rad), ay - 24 * math.sin(rad)],
               **stroke(7, color=RUBRIC, cap="butt"))
        p.text([cx - 60, ay + 36, 120, 12], f"PEN AT {ang}°", style="labelC")
    caption(p, [M, 966, CW, 40],
            "One skeleton, three tools. The straight pen yields the rational, "
            "vertical-axis letter; the slanted pen yields the humanist one. Type "
            "classification begins as pen angle.")


def pg_serif_text(b):
    p = new_page(b, "serif-1", chapter=CH1)
    y = section_head(p, 96, "1.4 · THE SERIF", "A stroke, finished",
                     lead="Serifs are not ornament. They are what the end of a stroke "
                          "looks like when it is properly concluded.")
    p.text([M, y, CW, 150],
           "Write the skeleton letters with a formal pen and, as Johnston observed, the "
           "result “looks incomplete and unfinished; and it is obvious that the thin "
           "strokes, at least, require marked terminals or serifs.” The serif enters "
           "the alphabet as a necessity of craft — the pen's way of beginning and ending "
           "cleanly — and only later hardens into a stylistic marker.",
           style="body")
    p.text([M, y + 172, CW, 128],
           "His rule for the finest work is exact: “serifs are treated as the actual "
           "finishing and shaping of the ends of the stems and branches, rather than as "
           "added parts.” A good serif belongs to its stem the way a capital belongs "
           "to its column. And note the corollary: “serifs of some sort are "
           "practically essential to the proper characterisation of an alphabet” — "
           "even the sans serif is a decision about serifs.",
           style="body")
    p.text([M, y + 320, CW, 96],
           "The plate opposite is a taxonomy of endings: five ways to conclude the same "
           "stem, from the pen's own triangular head to the abrupt stop of the "
           "grotesque. Each one implies a whole alphabet around it.",
           style="body")


def pg_serif_plate(b):
    p = new_page(b, "serif-2", chapter=CH1)
    framed_panel(p, [M, 90, CW, 850])
    names = ["BRACKETED", "HAIRLINE", "SLAB", "WEDGE", "SANS"]
    notes = ["old-style: the pen's fillet, smoothed by the chisel",
             "didone: contrast pushed to its rational extreme",
             "egyptian: the serif as a beam", "glyphic: the chisel's exit cut",
             "grotesque: the ending as a full stop"]
    colw = CW / 5
    y0, y1 = 260, 620  # stem top/bottom (baseline y1)
    sw = 26            # stem width
    for i, (nm, nt) in enumerate(zip(names, notes)):
        cx = M + i * colw + colw / 2
        p.text([M + i * colw, 140, colw, 16], nm, style="labelRC")
        p.text([M + i * colw + 8, 168, colw - 16, 60], nt, style="capC")
        if i == 0:  # bracketed: stem + serifs + straight fillet brackets
            p.rect([cx - sw / 2, y0, sw, y1 - y0], fill=INK)
            p.rect([cx - 40, y0 - 6, 80, 6], fill=INK)
            p.rect([cx - 40, y1, 80, 6], fill=INK)
            p.polygon([[cx - sw / 2, y0], [cx - sw / 2 - 16, y0],
                       [cx - sw / 2, y0 + 18]], fill=INK)
            p.polygon([[cx + sw / 2, y0], [cx + sw / 2 + 16, y0],
                       [cx + sw / 2, y0 + 18]], fill=INK)
            p.polygon([[cx - sw / 2, y1], [cx - sw / 2 - 16, y1],
                       [cx - sw / 2, y1 - 18]], fill=INK)
            p.polygon([[cx + sw / 2, y1], [cx + sw / 2 + 16, y1],
                       [cx + sw / 2, y1 - 18]], fill=INK)
        elif i == 1:  # hairline
            p.rect([cx - sw / 2, y0, sw, y1 - y0], fill=INK)
            p.rect([cx - 44, y0 - 4, 88, 4], fill=INK)
            p.rect([cx - 44, y1, 88, 4], fill=INK)
        elif i == 2:  # slab
            p.rect([cx - sw / 2, y0, sw, y1 - y0], fill=INK)
            p.rect([cx - 44, y0 - 14, 88, 14], fill=INK)
            p.rect([cx - 44, y1, 88, 14], fill=INK)
        elif i == 3:  # wedge
            p.rect([cx - sw / 2, y0, sw, y1 - y0], fill=INK)
            p.polygon([[cx - 42, y0], [cx + 42, y0], [cx + sw / 2, y0 + 16],
                       [cx - sw / 2, y0 + 16]], fill=INK)
            p.polygon([[cx - 42, y1], [cx + 42, y1], [cx + sw / 2, y1 - 16],
                       [cx - sw / 2, y1 - 16]], fill=INK)
        else:  # sans
            p.rect([cx - sw / 2, y0, sw, y1 - y0], fill=INK)
    p.line([M + 30, y1], [M + CW - 30, y1], **stroke(1, color=HAIR))
    p.text([M + CW - 96, y1 + 8, 90, 12], "BASELINE", style="label")
    # live-type confirmation row
    ty = 690
    fams = [F_GARALDE, F_DIDONE, ["DejaVu Serif", "serif"], ["Bitstream Charter", "serif"],
            F_NEOGROT]
    for i, fam in enumerate(fams):
        p.text([M + i * colw, ty, colw, 120], "I l", font_family=fam, font_size=96,
               color=INK, align="center")
    p.text([M + 30, ty + 132, CW - 60, 14],
           "THE SAME ENDINGS, ALIVE IN INSTALLED TYPE — GARALDE · DIDONE · SLAB-ISH · WEDGE · SANS",
           style="labelC")
    caption(p, [M, 966, CW, 40],
            "Five conclusions for one stem. Read a typeface's serifs first: they tell "
            "you its tool, its era, and most of its manners.")


def pg_anatomy_lines(b):
    p = new_page(b, "anatomy-1", chapter=CH1)
    y = section_head(p, 96, "1.5 · ANATOMY", "The lines every letter lives on",
                     lead="Before naming the parts of letters, name the lines that "
                          "govern them.")
    # big word on its guides
    panel_y = y + 16
    framed_panel(p, [M, panel_y, CW, 420])
    base = panel_y + 264
    xh = 108           # x-height
    cap = 168          # cap height
    asc = 188          # ascender
    dsc = 92           # descender
    guides = [
        (base, "BASELINE", "the line all letters stand on"),
        (base - xh, "X-HEIGHT", "top of the small letters' bodies"),
        (base - cap, "CAP HEIGHT", "top of the capitals"),
        (base - asc, "ASCENDER", "b d f h k l rise here"),
        (base + dsc, "DESCENDER", "g j p q y fall here"),
    ]
    for gy, nm, nt in guides:
        p.line([M + 24, gy], [M + CW - 24, gy],
               **stroke(1.2, color=(RUBRIC if nm == "BASELINE" else HAIR)))
        p.text([M + 28, gy - 15, 110, 11], nm, style="labelR")
        p.text([M + 140, gy - 15, 240, 11], nt, style="cap")
    p.text([M + 60, base - 233, 620, 300], "handglove Bx",
           font_family=SERIF, font_size=118, color=INK)
    caption(p, [M, panel_y + 446, CW, 40],
            "The five governing lines. The ratio of x-height to cap height — here a "
            "generous Garamond — is the single strongest determinant of how big a "
            "typeface looks at a given size.")
    p.text([M, panel_y + 520, CW, 150],
           "Two faces set at the same nominal size can differ wildly on the page, "
           "because the size measures the body, not the letters. What the eye reads is "
           "x-height. A face with a tall x-height (most screen sans) looks larger, sits "
           "denser, and needs more leading; a face with long ascenders and a small eye "
           "(the old-styles) looks smaller and airier at the same setting. Compare "
           "typefaces on their x-height, never on their point size.",
           style="body")


def pg_anatomy_parts(b):
    p = new_page(b, "anatomy-2", chapter=CH1)
    framed_panel(p, [M, 90, CW, 620])
    # Big drawn R and g with labelled parts (drawn => labels are exact)
    ry, s = 170, 300
    x_r = M + 70
    draw_skeleton(p, "R", x_r, ry, s, w=9)
    x_g = M + 390
    draw_skeleton(p, "g", x_g, ry + s * 0.0, s * 0.72, w=8)
    ann = [
        # (from-x, from-y, text-x, text-y, label)
        (x_r + 4, ry + s * 0.5, x_r - 44, ry + s * 0.5 - 60, "STEM"),
        (x_r + s * 0.53, ry + s * 0.26, x_r + s * 0.62, ry - 26, "BOWL"),
        (x_r + s * 0.30, ry + s * 0.26, x_r + s * 0.16, ry + s * 0.30, "COUNTER"),
        (x_r + s * 0.55, ry + s * 0.78, x_r + s * 0.62, ry + s * 0.94, "LEG / TAIL"),
        (x_g + s * 0.20, ry + s * 0.49 * 0.72 + 0.0, x_g - 40, ry + s * 0.30, "BOWL"),
        (x_g + s * 0.42 * 0.72, ry + s * 0.72 * 0.72, x_g + s * 0.52, ry + s * 0.62, "LINK"),
        (x_g + s * 0.22 * 0.72, ry + s * 1.06 * 0.72, x_g - 10, ry + s * 0.92, "LOOP"),
    ]
    for fx, fy, tx, ty, nm in ann:
        p.line([fx, fy], [tx + 26, ty + 14], **stroke(1, color=RUBRIC))
        p.circle([fx, fy], 3, fill=RUBRIC)
        p.text([tx - 20, ty, 110, 12], nm, style="labelR")
    # terminology strip in live type
    ty2 = 560
    p.text([M + 40, ty2, CW - 80, 60], "Rg  aefkty",
           font_family=SERIF, font_size=64, color=SUB, align="center")
    p.text([M + 30, ty2 + 84, CW - 60, 13],
           "APERTURE · ARM · EAR · SPINE · SPUR · TERMINAL — THE FULL LEXICON RUNS TO DOZENS",
           style="labelC")
    caption(p, [M, 736, CW, 40],
            "The named parts, pointed at exactly. A vocabulary is a tool of seeing: "
            "once you can say bowl, counter, link and loop, you can no longer not "
            "see them.")
    p.text([M, 812, CW, 130],
           "Anatomy earns its keep in comparison. Two faces differ where their parts "
           "differ — the angle of the e's bar, the openness of the c's aperture, the "
           "shape of the g's loop. Reviewing a typeface without this vocabulary is "
           "wine-tasting without words: you can like it, but you cannot say why, and "
           "you certainly cannot choose its replacement.",
           style="body")


def pg_qualities_text(b):
    p = new_page(b, "qualities-1", chapter=CH1)
    y = section_head(p, 96, "1.6 · THE NINE QUALITIES",
                     "What makes lettering good",
                     lead="Johnston compressed the whole judgement of lettering into "
                          "one sentence and one table.")
    quote_block(p, y + 6,
                "The first general virtue of lettering is readableness, the second, "
                "fitness for a given Use.", None)
    p.text([M, y + 96, CW, 14], "WRITING & ILLUMINATING, & LETTERING — CH. XIV", style="attr")
    p.text([M, y + 140, CW, 150],
           "Everything else serves those two. A piece of lettering that has "
           "readableness, beauty and character “has the essential virtues of good "
           "lettering” — and each of those three virtues, he found, rests on three "
           "qualities. The resulting table of nine is the most compact instrument of "
           "typographic criticism ever written, and it applies unchanged to a website, "
           "a wayfinding system, or a book.",
           style="body")
    p.text([M, y + 310, CW, 130],
           "Note the deliberate order. Readableness is “the only sound basis for a "
           "practical theory of lettering”; beauty is admitted second; character — "
           "the personal, the expressive — comes last, as “the product of a "
           "particular hand & tool at work in a particular craft.” Style is "
           "earned, not applied. The table may be used, he says, “as a test of the "
           "qualities of any piece of lettering — whether Manuscript, Printing, or "
           "Engraving.”",
           style="body")
    p.text([M, y + 470, CW, 60],
           "The next three pages take the virtues in turn, each with its own "
           "demonstrations.",
           style="body")


def pg_qualities_table(b):
    p = new_page(b, "qualities-2", chapter=CH1)
    framed_panel(p, [M, 90, CW, 850])
    groups = [
        ("READABLENESS", RUBRIC, [
            ("1 · Simplicity", "having no unnecessary parts"),
            ("2 · Distinctiveness", "the distinguishing marks of each letter strongly made"),
            ("3 · Proportion", "no part of a letter wrongly exaggerated or dwarfed"),
        ]),
        ("BEAUTY", GOLD, [
            ("4 · Beauty of Form", "each letter an individual and living whole, not a collection of parts"),
            ("5 · Beauty of Uniformity", "the family likeness of letters, so that they go well together"),
            ("6 · Beauty of Arrangement", "fitness in placing, connecting and spacing; in the disposal of the whole"),
        ]),
        ("CHARACTER", SUB, [
            ("7 · Essential Qualities of the Hand", "genuine work, the direct outcome of a rightly handled tool"),
            ("8 · Freedom", "skilled and unaffected boldness"),
            ("9 · Personality", "the marks that distinguish one hand from another"),
        ]),
    ]
    y = 130
    for name, col, rows in groups:
        p.rect([M + 30, y, 8, 8 + 3 * 74 - 22], fill=col)
        p.text([M + 56, y - 2, 300, 16], name, style="h3")
        yy = y + 34
        for t, d in rows:
            p.text([M + 56, yy, 240, 40], t, style="label")
            p.text([M + 300, yy - 2, CW - 350, 44], d, style="bodyS")
            yy += 74
        y = yy + 22
    caption(p, [M, 966, CW, 40],
            "Johnston's table of the nine qualities (1906), restated. Three virtues, "
            "three qualities each; the order is the argument.")


def pg_readableness(b):
    p = new_page(b, "qualities-3", chapter=CH1)
    y = section_head(p, 96, "VIRTUE ONE", "Readableness",
                     lead="Simplicity, distinctiveness, proportion — the reader's "
                          "three rights.")
    yy = y + 10
    framed_panel(p, [M, yy, CW, 620])
    # Simplicity: plain vs over-elaborated
    p.text([M + 30, yy + 24, 200, 13], "SIMPLICITY", style="labelR")
    p.text([M + 30, yy + 48, 300, 80], "Quiet", font_family=SERIF, font_size=56, color=INK)
    p.text([M + 380, yy + 48, 330, 80], "Quiet", font_family=SERIF, font_size=56,
           color=INK, italic=True, decoration="underline", letter_spacing=6)
    p.text([M + 30, yy + 140, 300, 26], "no unnecessary parts", style="cap")
    p.text([M + 380, yy + 140, 300, 26], "slope + spacing + rule: three ornaments, no gain",
           style="cap")
    # Distinctiveness: I l 1, O 0, rn m
    p.text([M + 30, yy + 210, 260, 13], "DISTINCTIVENESS", style="labelR")
    p.text([M + 30, yy + 234, 320, 70], "Il1  O0  rn m", font_family=SERIF,
           font_size=44, color=INK)
    p.text([M + 380, yy + 234, 320, 70], "Il1  O0  rn m", font_family=F_MONO,
           font_size=38, color=INK)
    p.text([M + 30, yy + 320, 300, 26], "a garalde: I, l and 1 nearly merge", style="cap")
    p.text([M + 380, yy + 320, 300, 26], "a monospace draws them apart by design",
           style="cap")
    # Proportion: dwarfed and exaggerated parts on skeleton R
    p.text([M + 30, yy + 392, 260, 13], "PROPORTION", style="labelR")
    draw_skeleton(p, "R", M + 60, yy + 430, 120, w=4)
    # a wrong R: giant bowl, dwarfed leg
    xx = M + 300
    p.line([xx, yy + 430], [xx, yy + 550], **stroke(4, color=SUB, cap="round"))
    p.line([xx, yy + 430], [xx + 66, yy + 430], **stroke(4, color=SUB, cap="round"))
    p.arc([xx + 66, yy + 478], 48, -90, 90, fill="none", **stroke(4, color=SUB, cap="round"))
    p.line([xx + 66, yy + 526], [xx, yy + 526], **stroke(4, color=SUB, cap="round"))
    p.line([xx + 30, yy + 526], [xx + 52, yy + 550], **stroke(4, color=SUB, cap="round"))
    p.text([xx + 130, yy + 470, 280, 60],
           "“no part wrongly exaggerated or dwarfed” — the bowl has eaten the leg",
           style="cap")
    caption(p, [M, yy + 646, CW, 40],
            "Readableness dissected. Each failure is local — one ornament, one confusable "
            "pair, one swollen bowl — but the reader pays for all of them together.")


def pg_beauty(b):
    p = new_page(b, "qualities-4", chapter=CH1)
    y = section_head(p, 96, "VIRTUE TWO", "Beauty",
                     lead="Form, uniformity, arrangement — beauty as the well-being "
                          "of the whole page.")
    p.text([M, y, CW, 104],
           "Uniformity is the quality Johnston explains with the most care, because it "
           "is the one that directly serves the reader: “Where the text letters are "
           "uniform, the reader is free to give his attention to the sense of the "
           "words.” The demonstration below sets the same sentence twice.",
           style="body")
    yy = y + 130
    framed_panel(p, [M, yy, CW, 430])
    p.text([M + 30, yy + 28, 300, 13], "UNIFORM — ONE FACE, ONE SIZE", style="labelR")
    p.text([M + 30, yy + 56, CW - 60, 90],
           "The letters serve the sentence, and the sentence carries the sense.",
           font_family=SERIF, font_size=25, color=INK, line_height=1.4)
    p.text([M + 30, yy + 170, 340, 13], "IRREGULAR — EVERY WORD ITS OWN VOICE", style="labelR")
    xx, wy = M + 30, yy + 200
    words = [("The", SERIF, 25, False), ("letters", F_MONO, 21, False),
             ("serve", SERIF, 31, True), ("the", F_NEOGROT, 22, False),
             ("sentence,", F_DIDONE, 27, False), ("and", SERIF, 18, False),
             ("the", F_GEOSANS, 24, False), ("sentence", SERIF, 25, True),
             ("carries", F_HUMSANS, 26, False), ("the", F_MONO, 19, False),
             ("sense.", F_DIDONE, 30, False)]
    for wd, fam, sz, it in words:
        wpx = 0.62 * sz * len(wd) * 0.85 + 10
        if xx + wpx > M + CW - 40:
            xx = M + 30
            wy += 54
        p.text([xx, wy, wpx + 14, 44], wd, font_family=fam, font_size=sz, color=INK,
               italic=it)
        xx += wpx + 12
    p.text([M + 30, yy + 350, CW - 60, 60],
           "“the variations in an irregular or changing text are distracting” — "
           "the reader spends on the costume what was meant for the meaning.",
           style="cap")
    caption(p, [M, yy + 456, CW, 40],
            "Beauty of uniformity, proved by its absence. Nothing in the second setting "
            "is individually ugly; the ugliness is the disagreement.")
    p.text([M, yy + 520, CW, 130],
           "Arrangement — the third quality — extends the same family likeness to the "
           "page: “a general fitness in the placing, connecting, and spacing of "
           "letters, words, and lines, in the disposal of the lettering in the given "
           "space, and in the proportioning of every part.” It returns as our "
           "subject in §1.11 and §1.13.",
           style="body")


def pg_character(b):
    p = new_page(b, "qualities-5", chapter=CH1, chrome=False, folio=True, bg="night")
    p.text([M, 150, CW, 16], "VIRTUE THREE · CHARACTER", style="kickerC")
    display_quote(p, [110, 250, W - 220, 340],
                  "The whole duty of beautiful typography is not to substitute for "
                  "the beauty or interest of the thing thought … a beauty or "
                  "interest of its own, but … to win access for that communication "
                  "by the clearness and beauty of the vehicle.",
                  size=25, line_height=1.5, color=CREAM, align="center")
    p.text([0, 620, W, 14],
           "T. J. COBDEN-SANDERSON, THE BOOK BEAUTIFUL (1902), AS QUOTED BY JOHNSTON",
           style="attrC")
    p.rect([W / 2 - 30, 668, 60, 2], fill="rubric")
    p.text([140, 706, W - 280, 170],
           "Character — the hand, freedom, personality — is real, and the table honours "
           "it. But it is placed last, and this quotation is the reason. The letter is "
           "a vehicle; its finest expression is the one that carries the thought "
           "farthest with the least loss. Personality that taxes the reader is not "
           "character but costume.",
           font_family=SERIF, font_size=15, color="#d8c9ae", line_height=1.65,
           align="center")


def pg_proportion_text(b):
    p = new_page(b, "proportion-1", chapter=CH1)
    y = section_head(p, 96, "1.7 · PROPORTION", "The widths of the capitals",
                     lead="The Roman capitals do not share one width — and their "
                          "variety is the rhythm of every inscription since.")
    p.text([M, y, CW, 150],
           "Measure the Trajan letters and a system appears. The round letters — O, Q, "
           "C, G, D — fill a square. H, A, N, T, U and V take about four-fifths of it. "
           "B, E, F, L, P, R and S take roughly half. M spreads slightly beyond the "
           "square, W well beyond it; I and J are strokes. Johnston devotes a chapter "
           "to these widths because getting them wrong is the fastest way to make "
           "capitals look cheap: even spacing cannot rescue uneven proportion.",
           style="body")
    p.text([M, y + 190, CW, 128],
           "The temptation the system resists is equalisation. Signwriters and engineers "
           "recur, every few generations, to capitals of uniform width — they fit "
           "grids, they simplify layout — and every time, the eye reports the same "
           "verdict: the E looks fat, the M looks crushed, the O looks starved. The "
           "alphabet is a society of unequal citizens, and its justice is proportional.",
           style="body")
    p.text([M, y + 340, CW, 80],
           "The plate opposite draws the widths as architecture: each skeleton letter "
           "standing in its own bay, measured against the square of the O.",
           style="body")


def pg_proportion_plate(b):
    p = new_page(b, "proportion-2", chapter=CH1)
    framed_panel(p, [M, 90, CW, 700])
    s = 96
    groups = [
        ("THE FULL SQUARE", ["O", "C", "G", "D"], "the round letters claim the whole"),
        ("ABOUT FOUR-FIFTHS", ["H", "A", "N", "V", "T"], "the rational middle class"),
        ("ABOUT ONE-HALF", ["E", "S", "B", "P", "R"], "the narrow letters"),
        ("BEYOND THE SQUARE", ["M", "W"], "the wide letters — and I, the stroke"),
    ]
    y = 150
    for name, letters, note in groups:
        p.text([M + 30, y - 26, 300, 12], name, style="labelR")
        p.text([M + 330, y - 26, CW - 360, 12], note, style="cap")
        x = M + 44
        # reference square (O's square) behind each letter
        for ch in letters:
            adv, _ = SKEL[ch]
            p.rect([x, y, s, s], fill="none", **stroke(1, color=HAIR))
            draw_skeleton(p, ch, x + (s - adv * s) / 2 if adv <= 1 else x, y, s, w=3.2)
            if adv <= 1:
                p.rect([x + (s - adv * s) / 2, y + s + 8, adv * s, 4], fill=RUBRIC)
            else:
                p.rect([x, y + s + 8, min(adv * s, s * 1.1), 4], fill=RUBRIC)
            x += s + 22
        if name == "BEYOND THE SQUARE":
            p.rect([x, y, s, s], fill="none", **stroke(1, color=HAIR))
            draw_skeleton(p, "I", x + s / 2 - 5, y, s, w=3.2)
            p.rect([x + s / 2 - 5, y + s + 8, 10, 4], fill=RUBRIC)
        y += s + 66
    caption(p, [M, 816, CW, 40],
            "The widths of the capitals, measured against the square of the O — an "
            "author's drawing after the monumental tradition Johnston measured (ch. "
            "XV). The red bars are the letters' true advances.")
    p.text([M, 886, CW, 44],
           "“The proportions of large CAPITALS … follow the fine monumental "
           "type” — and every revival, from Jenson to yesterday, re-measures them.",
           style="bodyS")


def pg_classification_text(b):
    p = new_page(b, "class-1", chapter=CH1)
    y = section_head(p, 96, "1.8 · TAXONOMY", "A taxonomy of typefaces",
                     lead="Classes of type are episodes in the biography of the tool.")
    p.text([M, y, CW, 190],
           "Once you have seen the pen inside the letter, classification stops being "
           "connoisseurship and becomes reading. The humanist and garalde faces keep "
           "the slanted pen's oblique axis and gentle contrast. The transitionals "
           "straighten the axis as the pointed pen and the engraver arrive. The "
           "didones push contrast to the rational extreme — hairlines against pillars. "
           "The slabs armour the serif for advertising; the grotesques remove it; the "
           "geometric sans redraws the skeleton with compass and rule; the humanist "
           "sans lets the pen back in without its serifs. And the monospace submits "
           "every letter to one width — the typewriter's discipline.",
           style="body")
    p.text([M, y + 218, CW, 100],
           "The five plates that follow are specimens in the honest sense: each class "
           "shown in a typeface genuinely of that class, with its diagnostic features "
           "called out. Learn one face per class and you can place almost anything.",
           style="body")
    # small preview strip
    yy = y + 350
    framed_panel(p, [M, yy, CW, 170])
    fams = [("GARALDE", F_GARALDE), ("DIDONE", F_DIDONE), ("GROTESQUE", F_GROT),
            ("HUMANIST SANS", F_HUMSANS), ("MONO", F_MONO)]
    colw = CW / 5
    for i, (nm, fam) in enumerate(fams):
        p.text([M + i * colw, yy + 20, colw, 90], "Aa", font_family=fam, font_size=64,
               color=INK, align="center")
        p.text([M + i * colw, yy + 124, colw, 12], nm, style="labelC")
    caption(p, [M, yy + 196, CW, 30],
            "Five classes, five living specimens — the itinerary of the next five plates.")


def _specimen_page(b, pid, *, class_name, face_name, fam, era, traits, sample_note,
                   provenance=None,
                   pangram="How vexingly quick daft zebras jump!"):
    p = new_page(b, pid, chapter=CH1)
    p.text([M, 90, CW, 14], f"SPECIMEN · {class_name}", style="kicker")
    p.text([M, 118, CW, 54], face_name, style="h2")
    p.text([M, 168, CW, 16], era, style="attr")
    framed_panel(p, [M, 210, CW, 330])
    p.text([M + 24, 222, 330, 300], "Aa", font_family=fam, font_size=230, color=INK)
    ty = 246
    for tr in traits:
        lines = max(1, -(-len(tr) // 34))
        p.text([M + 366, ty, CW - 400, lines * 21 + 4], tr, style="bodyS")
        ty += lines * 21 + 10
    # alphabet
    p.text([M, 570, CW, 46], "ABCDEFGHIJKLMNOPQRSTUVWXYZ", font_family=fam,
           font_size=27, color=INK)
    p.text([M, 620, CW, 46], "abcdefghijklmnopqrstuvwxyz 0123456789",
           font_family=fam, font_size=27, color=INK)
    p.rect([M, 676, CW, 1], fill="hair")
    # size ladder
    y = 700
    for sz in (12, 16, 22, 30):
        p.text([M, y, CW, sz * 1.5], pangram, font_family=fam, font_size=sz, color=INK)
        p.text([M + CW - 34, y + sz * 0.2, 34, 12], f"{sz}", style="tocn")
        y += sz * 1.7 + 8
    caption(p, [M, y + 14, CW, 40], sample_note)
    if provenance:
        p.text([M, y + 62, CW, 30], provenance,
               style={"class": "cap", "font_size": 9})
    return p


def pg_spec_garalde(b):
    _specimen_page(
        b, "spec-garalde",
        class_name="THE GARALDE (OLD-STYLE)",
        face_name="EB Garamond",
        fam=F_GARALDE,
        era="AFTER CLAUDE GARAMONT, PARIS, C. 1530 — REVIVED 2011",
        traits=[
            "· oblique stress — the slanted pen's axis",
            "· moderate contrast, bracketed serifs",
            "· small eye, long extenders — an airy page",
            "· the a and e carry the pen's diagonal",
            "The garalde is the book face: five hundred years of continuous service at text sizes.",
        ],
        sample_note="A garalde specimen. This whole book's text is set in it — the "
                    "specimen is the page you are reading.")


def pg_spec_didone(b):
    _specimen_page(
        b, "spec-didone",
        class_name="THE DIDONE (MODERN)",
        face_name="GFS Didot",
        fam=F_DIDONE,
        era="AFTER FIRMIN DIDOT, PARIS, C. 1784",
        traits=[
            "· vertical stress — the pen is gone",
            "· extreme contrast: hairline meets pillar",
            "· flat, unbracketed serifs",
            "· cold brilliance; fashion's favourite",
            "Didones dazzle at display sizes and shimmer illegibly at small ones: the hairlines vanish before the stems do.",
        ],
        sample_note="A didone specimen. Note how the size ladder punishes it: by 12 "
                    "pixels the hairlines are dissolving into the paper.")


def pg_spec_grot(b):
    _specimen_page(
        b, "spec-grot",
        class_name="THE GROTESQUE & NEO-GROTESQUE",
        face_name="Inter",
        fam=F_NEOGROT,
        era="FROM THE ENGLISH FOUNDRIES, C. 1830 — RATIONALISED C. 1957",
        traits=[
            "· the serif removed; contrast nearly flat",
            "· closed apertures, upright axis",
            "· neutral by intention — the 'crystal goblet' argument in letter form",
            "The neo-grotesque is the default voice of interfaces: available everywhere, offending no one, saying little.",
        ],
        sample_note="A neo-grotesque specimen (Inter, drawn for screens). Its tall "
                    "x-height and open counters are the didone's exact opposite.")


def pg_spec_humanist(b):
    p = _specimen_page(
        b, "spec-humanist",
        class_name="THE HUMANIST & GEOMETRIC SANS",
        face_name="Gillius / Cabin",
        fam=F_HUMSANS,
        era="AFTER EDWARD JOHNSTON & ERIC GILL, LONDON, 1916–1928",
        traits=[
            "· the pen's skeleton, without serifs",
            "· open apertures, humane rhythm",
            "· Johnston's own block letter for the London Underground (1916) opened this line",
            "The geometric sans (below, in Cabin's rounder cousin) swaps the pen for the compass: pure circle, pure bar.",
        ],
        sample_note="Humanist sans above; a geometric line below. The humanist keeps "
                    "the two-storey a and g; the geometric prefers the circle.")
    p.text([M, 950, CW, 40], "geometry: O = ○, a = ◠ + |",
           font_family=F_GEOSANS, font_size=26, color=SUB)


def pg_spec_mono(b):
    p = new_page(b, "spec-mono", chapter=CH1)
    p.text([M, 90, CW, 14], "SPECIMEN · THE MONOSPACE", style="kicker")
    p.text([M, 118, CW, 54], "Fira Mono", style="h2")
    p.text([M, 168, CW, 16], "THE TYPEWRITER'S DISCIPLINE (PATENTED 1868) — THE TERMINAL'S, STILL",
           style="attr")
    framed_panel(p, [M, 210, CW, 360])
    # equal-advance demonstration: cells
    cell = (CW - 60) / 10
    for row, txt in enumerate(["iiiiiiiiii", "mmmmmmmmmm", "0123456789"]):
        yy = 240 + row * 100
        for i, ch in enumerate(txt):
            p.rect([M + 30 + i * cell, yy, cell, 84], fill="none",
                   **stroke(1, color=HAIR))
            p.text([M + 30 + i * cell, yy + 12, cell, 60], ch, font_family=F_MONO,
                   font_size=44, color=INK, align="center")
    caption(p, [M, 590, CW, 40],
            "One advance width for every glyph. The i is padded, the m squeezed — a "
            "deliberate injustice, repaid in alignment.")
    p.text([M, 660, CW, 130],
           "The monospace surrenders Johnston's proportion on purpose. Its reward is "
           "the column: figures that align without effort, code whose indentation is "
           "structure, tables that need no rules. This is why data belongs in a "
           "monospace — proportional figures wobble, tabular ones stand in ranks. "
           "Identifiers, code, and numbers are not prose; the change of face marks the "
           "change of register.",
           style="body")
    for i, ln in enumerate(["TOTAL      1,204,551", "MEAN          40,152",
                            "MEDIAN        38,090", "SPREAD        11,006"]):
        p.text([M, 810 + i * 22, CW, 18], ln, style="mono")


def pg_weight(b):
    p = new_page(b, "weight", chapter=CH1)
    y = section_head(p, 96, "1.9 · WEIGHT", "The family and its axis",
                     lead="A modern typeface is not one design but a family "
                          "spanning an axis of weight.")
    framed_panel(p, [M, y + 10, CW, 470])
    weights = [(200, "ExtraLight"), (300, "Light"), (400, "Regular"),
               (500, "Medium"), (600, "SemiBold"), (700, "Bold"), (800, "ExtraBold")]
    yy = y + 34
    for wgt, nm in weights:
        p.text([M + 30, yy, 430, 58], "Against gravity", font_family=F_NEOGROT,
               font_size=41, font_weight=wgt, color=INK)
        p.text([M + 500, yy + 16, 160, 12], f"{nm.upper()} · {wgt}", style="label")
        yy += 62
    caption(p, [M, y + 500, CW, 40],
            "One design, seven weights (Inter). The stems thicken; the skeleton — "
            "check the counters — barely moves.")
    p.text([M, y + 556, CW, 150],
           "Two rules keep a family honest. First, hierarchy moves one step at a time: "
           "regular against semibold reads as structure, regular against black reads "
           "as noise. Second, never synthesise. A slanted roman is not an italic and a "
           "smeared regular is not a bold; both are forgeries of the tool's work, and "
           "the eye convicts them at a glance.",
           style="body")


def pg_scale(b):
    p = new_page(b, "scale", chapter=CH1)
    y = section_head(p, 96, "1.10 · SCALE", "A modular scale for sizes",
                     lead="Sizes chosen one by one drift; sizes drawn from a ratio "
                          "agree before they are used.")
    base, ratio = 11.5, 1.25
    sizes = [round(base * ratio ** i, 1) for i in range(8)]
    names = ["caption", "body", "lead", "h3", "h2", "h1", "display", "cover"]
    framed_panel(p, [M, y + 10, CW, 560])
    yy = y + 40
    for sz, nm in zip(reversed(sizes), reversed(names)):
        bar_w = sz / sizes[-1] * (CW - 320)
        p.rect([M + 30, yy + 4, bar_w, 3], fill=PANEL2)
        p.text([M + 30, yy - sz * 1.1, CW - 240, sz * 1.6], "Harmony",
               font_family=SERIF, font_size=sz, color=INK)
        p.text([M + CW - 170, yy - 8, 150, 12], f"{nm.upper()} · {sz}", style="label")
        yy += max(sz * 1.35, 34) + 14
    caption(p, [M, y + 596, CW, 40],
            f"A modular scale: base {base}, ratio {ratio}. Every size is the ratio's "
            "power — the same discipline a chord imposes on notes.")
    p.text([M, y + 652, CW, 110],
           "The ratio is a voice: 1.2 is quiet and bookish, 1.25 assertive, 1.333 "
           "editorial, 1.5 poster-loud. What matters is not which you choose but that "
           "the whole document draws from one — scale is uniformity (Johnston's fifth "
           "quality) applied to size.",
           style="body")


def pg_spacing(b):
    p = new_page(b, "spacing", chapter=CH1)
    y = section_head(p, 96, "1.11 · SPACING", "Letters, words, lines",
                     lead="“The letters of a word are fitted together so that "
                          "there is a general effect of evenness.”")
    yy = y + 6
    framed_panel(p, [M, yy, CW, 600])
    # caps tracking
    p.text([M + 30, yy + 22, 400, 13], "CAPITALS WANT AIR", style="labelR")
    p.text([M + 30, yy + 46, CW - 60, 34], "ROMAN MONUMENT", font_family=SERIF,
           font_size=25, color=SUB, letter_spacing=0)
    p.text([M + 30, yy + 92, CW - 60, 34], "ROMAN MONUMENT", font_family=SERIF,
           font_size=25, color=INK, letter_spacing=5)
    p.text([M + CW - 200, yy + 52, 170, 12], "TRACKED ±0 — CROWDED", style="cap")
    p.text([M + CW - 200, yy + 98, 170, 12], "TRACKED +5 — EVEN", style="cap")
    # lowercase tracking (the sin)
    p.text([M + 30, yy + 160, 400, 13], "SMALL LETTERS WANT NONE", style="labelR")
    p.text([M + 30, yy + 184, CW - 60, 30], "a word is a fitted thing",
           font_family=SERIF, font_size=23, color=INK)
    p.text([M + 30, yy + 224, CW - 60, 30],
           "a  w o r d  i s  a  s c a t t e r e d  t h i n g",
           style={"font_family": SERIF, "font_size": 23, "color": SUB,
                  "letter_spacing": 5, "word_spacing": 18})
    # word spacing / massed writing
    p.text([M + 30, yy + 290, 400, 13], "THE WORD-SPACE IS A LETTER TOO", style="labelR")
    p.text([M + 30, yy + 314, CW - 60, 84],
           "Close, even setting lets letters support and enforce one another; the page "
           "reads as a woven texture — Johnston's ‘massed writing’.",
           font_family=SERIF, font_size=17, color=INK, line_height=1.4)
    p.text([M + 30, yy + 410, CW - 60, 84],
           "Loose,   gappy   setting   tears   the   texture   into   islands;   the "
           "eye   ferries   between   them   and   arrives   tired.",
           font_family=SERIF, font_size=17, color=SUB, line_height=1.75,
           letter_spacing=0.5)
    caption(p, [M, yy + 520, CW - 60, 60],
            "Spacing at three altitudes. Johnston: distinctiveness “necessitates "
            "sufficient interspaces” — but sufficiency, not surplus.")
    p.text([M, yy + 630, CW, 90],
           "The test for all of it is squint-evenness: blur your eyes and the block "
           "should read as one grey. Dark knots are crowding; white rivers are excess. "
           "The scribes checked their pages this way five centuries before we had a "
           "word for texture.",
           style="body")


def pg_measure(b):
    p = new_page(b, "measure", chapter=CH1)
    y = section_head(p, 96, "1.12 · MEASURE & LEADING", "The line and its breath",
                     lead="A line of text is a journey; the measure is its length "
                          "and the leading its rest.")
    yy = y + 6
    framed_panel(p, [M, yy, CW, 560])
    # measure ruler
    p.text([M + 30, yy + 22, 400, 13], "THE MEASURE — 45 TO 75 CHARACTERS", style="labelR")
    demo = "A narrow column of text is generally best, for short lines are easiest to read, and do not tire the eye in passing from one line to the next."
    p.rect([M + 30, yy + 46, CW - 60, 30], fill=PANEL2)
    p.rect([M + 30 + (CW - 60) * 0.28, yy + 46, (CW - 60) * 0.42, 30], fill="#e3d3ae")
    for i in range(0, 11):
        xx = M + 30 + (CW - 60) * i / 10
        p.line([xx, yy + 76], [xx, yy + 84], **stroke(1, color=SUB))
        p.text([xx - 14, yy + 88, 28, 10], str(i * 10), style="capC")
    p.text([M + 30, yy + 108, CW - 60, 26],
           "characters per line — the tinted band is home", style="cap")
    # leading comparison
    p.text([M + 30, yy + 160, 400, 13], "LEADING — THE SAME TEXT, THREE BREATHS", style="labelR")
    for i, (lh, verdict) in enumerate([(1.15, "TIGHT — lines shear"),
                                       (1.55, "RIGHT — the eye lands home"),
                                       (2.1, "LOOSE — lines drift apart")]):
        bx = M + 30 + i * (CW - 60) / 3
        bw = (CW - 60) / 3 - 20
        p.text([bx, yy + 186, bw, 150], demo[:90] + "…",
               font_family=SERIF, font_size=11.5, color=INK, line_height=lh)
        p.text([bx, yy + 350, bw, 24], verdict, style="cap")
    p.text([M + 30, yy + 400, CW - 60, 100],
           "Johnston's manuscript rule of thumb survives every technology change: "
           "short lines spare the hand and the eye; a wide page wants columns, or "
           "larger type, or wider margins — never merely longer lines.",
           font_family=SERIF, font_size=14.5, color=INK, line_height=1.55)
    caption(p, [M, yy + 586, CW, 40],
            "Measure and leading are one system: widen the line and you must open the "
            "leading; open the leading and you may widen the line.")


def pg_margins(b):
    p = new_page(b, "margins", chapter=CH1)
    y = section_head(p, 96, "1.13 · THE PAGE", "Margins & the opening",
                     lead="“Margins are necessary in order to isolate and frame "
                          "a text: thus they contribute to its legibility and beauty.”")
    framed_panel(p, [M, y + 10, CW, 520])
    # a book opening with the 1.5 : 2 : 3 : 4 canon
    u = 26
    ph, pw = 380, 268
    oy = y + 70
    ox = M + (CW - 2 * pw - 8) / 2
    for side in (0, 1):
        px = ox + side * (pw + 8)
        p.rect([px, oy, pw, ph], fill="#ffffff")
        p.rect([px, oy, pw, ph], fill="none", **stroke(1.2, color=SUB))
        inner, top, outer, foot = 1.5 * u * 0.8, 2 * u * 0.8, 3 * u * 0.8, 4 * u * 0.8
        tx = px + (inner if side == 1 else outer)
        tw = pw - inner - outer
        tyy = oy + top
        th = ph - top - foot
        p.rect([tx, tyy, tw, th], fill=PANEL2)
        for li in range(12):
            p.rect([tx + 8, tyy + 10 + li * (th - 20) / 12, tw - 16, 2], fill=SUB)
    p.line([ox + pw + 4, oy - 14], [ox + pw + 4, oy + ph + 14], **stroke(1, color=RUBRIC))
    labels = [("INNER 1½", ox + pw - 20, oy + ph + 26), ("TOP 2", ox + pw / 2, oy - 24),
              ("OUTER 3", ox - 46, oy + ph / 2), ("FOOT 4", ox + pw / 2, oy + ph + 26)]
    p.text([ox + pw - 44, oy + ph + 22, 110, 12], "INNER 1½ + 1½", style="label")
    p.text([ox + 60, oy - 30, 90, 12], "TOP 2", style="label")
    p.text([ox + 2 * pw + 20, oy + ph / 2 - 8, 64, 12], "OUTER 3", style="label")
    p.text([ox + 60, oy + ph + 22, 90, 12], "FOOT 4", style="label")
    caption(p, [M, y + 556, CW, 54],
            "Johnston's margin canon for the opening: inner 1½, top 2, outer 3, foot 4 "
            "— “proportions … common in early MSS.” The two inner margins "
            "combine to equal one outer: the opening, not the page, is the unit.")
    p.text([M, y + 636, CW, 120],
           "The canon encodes three observations. Readers hold books by the foot "
           "margin, so it is widest. The two pages of an opening read as one sheet "
           "with two columns, so the gutter halves. And text placed at the geometric "
           "centre appears to sag, so the top tightens. None of this expired with "
           "parchment — a screen's safe-areas rediscover it clause by clause.",
           style="body")


def pg_ch1_close(b):
    p = new_page(b, "ch1-close", chapter=CH1, chrome=False, folio=True)
    p.text([M, 170, CW, 16], "CHAPTER I · A SUMMARY IN FIVE LINES", style="kickerC")
    lines = [
        "The skeleton is common property; the tool gives it voice.",
        "Readableness first, fitness second — beauty and character serve them.",
        "Serifs, weight and contrast are the fossil record of the pen.",
        "Uniformity frees the reader; proportion is the alphabet's justice.",
        "Space — in the word, the line, the margin — is a material, not an absence.",
    ]
    y = 280
    for i, ln in enumerate(lines):
        p.text([M + 40, y, 40, 40], ["I", "II", "III", "IV", "V"][i], style="h3r")
        p.text([M + 110, y - 4, CW - 150, 60], ln, style="lead")
        y += 92
    p.rect([W / 2 - 30, y + 30, 60, 2], fill="rubric")
    skeleton_word(p, "AHMORS", 190, y + 100, 48, w=2.8, color=SUB)
    p.text([0, y + 220, W, 14], "THE HUE FOLLOWS — FOR NO LETTER STANDS ON NOTHING",
           style="attrC")


# --------------------------------------------------------------------------- #
# CHAPTER II — THE HUE (pp. 37-58)                                             #
# --------------------------------------------------------------------------- #

CH2 = "Chapter II · The Hue"
CH3 = "Chapter III · The Arrangement"
CH4 = "Chapter IV · Colour at Work"


def luma_grey(hx: str) -> str:
    r, g, bch = (int(hx[i:i + 2], 16) for i in (1, 3, 5))
    L = round(0.2126 * r + 0.7152 * g + 0.0722 * bch)
    return f"#{L:02x}{L:02x}{L:02x}"


def wcag_ratio(fg: str, bg: str) -> float:
    """WCAG 2.x contrast ratio between two hex colours."""
    def lum(hx):
        cs = []
        for i in (1, 3, 5):
            c = int(hx[i:i + 2], 16) / 255
            cs.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
        return 0.2126 * cs[0] + 0.7152 * cs[1] + 0.0722 * cs[2]
    l1, l2 = sorted((lum(fg), lum(bg)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def hue(name: str) -> str:
    return dict(WHEEL)[name]


def draw_wheel(p, cx, cy, r, *, inner=0.55, labels=False, ring_color=None):
    for i, (nm, hx) in enumerate(WHEEL):
        p.sector([cx, cy], r, i * 30 - 90, (i + 1) * 30 - 90, fill=hx)
    p.circle([cx, cy], r * inner, fill="paper")
    if ring_color:
        p.circle([cx, cy], r + 8, fill="none", **stroke(2, color=ring_color))
    if labels:
        for i, (nm, hx) in enumerate(WHEEL):
            a = math.radians(i * 30 + 15)
            lx, ly = cx + (r + 34) * math.sin(a), cy - (r + 34) * math.cos(a)
            p.text([lx - 56, ly - 7, 112, 12], nm.upper(), style="labelC")


def pg_ch2_opener(b):
    p = new_page(b, "ch2-opener", chrome=False, folio=False, bg="night")
    p.text([M, 200, CW, 24], "CHAPTER II", style="kickerC")
    p.text([0, 240, W, 170], "II", style={"class": "cnum", "align": "center"})
    p.text([0, 430, W, 80], "The Hue",
           style={"class": "hcover", "color": "#f4ead6"})
    p.rect([W / 2 - 30, 560, 60, 2], fill="rubric")
    p.text([120, 610, W - 240, 90],
           "On tones, scales and hues; on the law by which neighbouring colours "
           "change one another; and on the six harmonies that follow from it.",
           style={"class": "leadC", "color": "#d8c9ae"})
    # hue band
    bw = (W - 2 * 148) / 12
    for i, (_, hx) in enumerate(WHEEL):
        p.rect([148 + i * bw, 790, bw - 4, 44], fill=hx)


def pg_vocab_text(b):
    p = new_page(b, "vocab", chapter=CH2)
    y = section_head(p, 96, "2.1 · VOCABULARY", "Tones, scales & hues",
                     lead="Chevreul begins where every science begins: by refusing "
                          "to use words loosely.")
    p.text([M, y, CW, 128],
           "“The words tones and hues recur continually, both in common language "
           "and in that of artists; yet they are not so well defined as to be free from "
           "ambiguity.” His repair is a three-term system, and it has never been "
           "bettered for practical work:",
           style="body")
    yy = y + 140
    defs = [
        ("TONE", "the modifications a colour “in its greatest intensity, is capable "
                 "of receiving from white, which lowers its tone, or of black, which "
                 "heightens it.”"),
        ("SCALE", "“the assemblage of tones of the same colour, thus modified. The "
                  "pure colour is the normal tone of the scale.”"),
        ("HUE", "“the modifications which a colour receives from the addition of a "
                "small quantity of another” — blue warmed toward green, red pushed "
                "toward orange."),
    ]
    for nm, d in defs:
        p.rect([M, yy + 4, 8, 54], fill="rubric")
        p.text([M + 26, yy, 110, 16], nm, style="h3r")
        p.text([M + 150, yy - 2, CW - 150, 78], d, style="bodyS")
        yy += 92
    p.text([M, yy + 16, CW, 128],
           "The system's power is that it separates three axes the eye tends to blur: "
           "how light or dark (tone), which colour family (scale), and which direction "
           "the family leans (hue). A designer who says “make it a deeper tone of "
           "the same scale” has asked for one change; one who says “make it "
           "darker blue” may receive three.",
           style="body")
    p.text([M, yy + 160, CW, 60],
           "The plate opposite draws three complete scales, with each normal tone — "
           "the pure colour — marked in its rank.",
           style="body")


def pg_tone_scales_plate(b):
    p = new_page(b, "tone-scales", chapter=CH2)
    framed_panel(p, [M, 90, CW, 700])
    scales = [("THE RED SCALE", hue("Red")), ("THE BLUE SCALE", hue("Blue")),
              ("THE YELLOW SCALE", hue("Yellow"))]
    steps = [-0.82, -0.62, -0.42, -0.21, 0.0, 0.2, 0.4, 0.6, 0.78]
    bw = (CW - 120) / len(steps)
    y = 150
    for nm, hx in scales:
        p.text([M + 60, y - 24, 400, 13], nm, style="labelR")
        for i, t in enumerate(steps):
            x = M + 60 + i * bw
            p.rect([x, y, bw - 6, 120], fill=tone(hx, t))
            if t == 0.0:
                p.rect([x, y, bw - 6, 120], fill="none", **stroke(2.5, color=INK))
                p.text([x, y + 128, bw - 6, 12], "NORMAL", style="labelC")
        y += 200
    p.text([M + 60, y - 12, CW - 120, 34],
           "← lowered with white · · · the pure colour · · · heightened with black →",
           style="capC")
    caption(p, [M, 816, CW, 40],
            "Three scales of nine tones. Chevreul: tones run “from white to dark "
            "brown” through the colour's full intensity; the framed chip is each "
            "scale's normal tone.")
    p.text([M, 886, CW, 84],
           "Note what the plate makes obvious: the normal tone does not sit at the "
           "same rank in every scale. Yellow reaches full intensity while still light; "
           "blue arrives deep. Scales are not interchangeable ladders — a fact the "
           "harmonies of §2.6 will exploit.",
           style="body")


def pg_wheel_plate(b):
    p = new_page(b, "wheel", chapter=CH2)
    y = section_head(p, 96, "2.2 · THE WHEEL", "Twelve hues in a circle",
                     lead="Arrange the hues by neighbourhood and the circle draws "
                          "itself — with red, yellow and blue as its poles.")
    draw_wheel(p, W / 2, y + 300, 212, labels=True)
    p.text([W / 2 - 60, y + 280, 120, 40], "R · Y · B", style="labelRC")
    caption(p, [M, y + 570, CW, 54],
            "Chevreul's painter's wheel: red, yellow and blue as "
            "primaries; orange, green and violet mixed between them; twelve named "
            "stations in all. Neighbours share pigment; opposites share nothing.")
    p.text([M, y + 650, CW, 150],
           "The wheel is not a fact of physics — light's spectrum is a line, not a "
           "loop — but it is a fact of pigment and of perception, and it is the map on "
           "which every harmony in this chapter is plotted. Distance on the wheel "
           "is difference to the eye.",
           style="body")


def pg_law_text(b):
    p = new_page(b, "law", chapter=CH2)
    p.text([M, 110, CW, 16], "2.3 · THE LAW", style="kicker")
    p.text([M, 140, CW, 60], "Simultaneous contrast", style="h2")
    display_quote(p, [M, 224, CW, 250],
                  "All the phenomena I have observed seem to depend upon a very "
                  "simple law: when two contiguous colours are seen at the same "
                  "time, they appear as dissimilar as possible, both with regard "
                  "to their optical composition and their depth of tone.",
                  size=24, line_height=1.5)
    p.text([M, 486, CW, 14],
           "M. E. CHEVREUL, 1839 — PART I, CH. II: THE LAW AND ITS FORMULA",
           style="attr")
    p.rect([M, 516, 64, 2], fill="rubric")
    p.text([M, 546, CW, 170],
           "The discovery began as a complaint. Weavers at the Gobelins manufactory "
           "reported that Chevreul's black yarns looked feeble next to blue — and his "
           "chemistry found nothing wrong with the dye. The defect was in the "
           "neighbourhood: beside blue, any grey or black borrows blue's complement "
           "and warms toward orange. The dye was innocent; the juxtaposition was "
           "guilty.",
           style="body")
    p.text([M, 740, CW, 170],
           "The law has two blades. Contrast of tone: beside a dark, a colour looks "
           "lighter; beside a light, darker. Contrast of colour: each neighbour tints "
           "the other with its own complement — red pushes its neighbours toward "
           "green, blue pushes them toward orange. No colour on any page is ever "
           "seen alone; the ground is always a co-author. The next plate lets you "
           "catch the law in the act.",
           style="body")


def pg_simultaneous_plate(b):
    p = new_page(b, "simultaneous", chapter=CH2)
    framed_panel(p, [M, 90, CW, 620])
    grounds = [("#ffffff", "ON WHITE — darkest"), ("#17130e", "ON BLACK — lightest"),
               (hue("Yellow"), "ON YELLOW — leans violet"),
               (hue("Blue"), "ON BLUE — leans orange"),
               (hue("Red"), "ON RED — leans green"),
               (hue("Green"), "ON GREEN — leans red")]
    GREY = "#8a8378"
    colw = (CW - 60) / 3
    for i, (g, nt) in enumerate(grounds):
        x = M + 30 + (i % 3) * colw
        yy = 130 + (i // 3) * 250
        p.rect([x, yy, colw - 20, 190], fill=g)
        p.rect([x + (colw - 20) / 2 - 34, yy + 61, 68, 68], fill=GREY)
        p.text([x, yy + 198, colw - 20, 24], nt, style="capC")
    p.text([M + 30, 640, CW - 60, 40],
           "ONE GREY — SIX APPEARANCES. THE CHIP IS IDENTICAL IN EVERY PANEL.",
           style="labelC")
    caption(p, [M, 736, CW, 40],
            "The law in the act. Each ground pushes the same grey toward its own "
            "complement and away from its own tone — “as dissimilar as possible.”")
    # fluting demonstration
    yy = 810
    steps = 8
    bw = CW / steps
    for i in range(steps):
        p.rect([M + i * bw, yy, bw + 0.5, 90], fill=tone(hue("Blue"), -0.85 + i * 0.2))
    p.text([M, yy + 100, CW, 44],
           "Eight flat bands — yet each edge appears fluted: every band darkens "
           "toward its lighter neighbour and lightens toward its darker one.",
           style="cap")


def pg_successive_plate(b):
    p = new_page(b, "successive", chapter=CH2)
    y = section_head(p, 96, "2.4 · SUCCESSIVE CONTRAST", "The eye replies",
                     lead="Stare, look away, and the eye answers every colour "
                          "with its opposite.")
    framed_panel(p, [M, y + 10, CW, 420])
    cx1 = M + CW * 0.28
    cx2 = M + CW * 0.72
    cy = y + 190
    p.circle([cx1, cy], 96, fill=hue("Red"))
    p.line([cx1 - 12, cy], [cx1 + 12, cy], **stroke(2.5, color=PAPER))
    p.line([cx1, cy - 12], [cx1, cy + 12], **stroke(2.5, color=PAPER))
    p.text([cx1 - 120, cy + 122, 240, 14], "1 · FIX THE CROSS FOR 30 SECONDS",
           style="labelC")
    # arrow
    p.line([cx1 + 130, cy], [cx2 - 130, cy], **stroke(2, color=SUB))
    p.polygon([[cx2 - 130, cy - 6], [cx2 - 118, cy], [cx2 - 130, cy + 6]], fill=SUB)
    p.circle([cx2, cy], 96, fill="#ffffff")
    p.circle([cx2, cy], 96, fill="none", **stroke(1.4, color=HAIR))
    p.circle([cx2, cy], 96, fill=mix(hue("Green"), "#ffffff", 0.86))
    p.line([cx2 - 12, cy], [cx2 + 12, cy], **stroke(2.5, color=SUB))
    p.line([cx2, cy - 12], [cx2, cy + 12], **stroke(2.5, color=SUB))
    p.text([cx2 - 120, cy + 122, 240, 14], "2 · THE GHOST THE EYE SUPPLIES",
           style="labelC")
    caption(p, [M, y + 456, CW, 54],
            "Successive contrast. The fatigued eye discharges red's complement — a "
            "green ghost (printed here at the strength most observers report). "
            "Chevreul distinguished it carefully from the simultaneous kind, and named "
            "their combination mixed contrast.")
    p.text([M, y + 540, CW, 130],
           "The practical consequence is real: a reader leaving a saturated red panel "
           "carries a green bias onto whatever follows. Sequences of intense colour — "
           "slides, packaging walls, app onboarding — are experienced through this "
           "after-glow. Chevreul's advice stands: let a neutral interval discharge the "
           "eye before the next strong statement.",
           style="body")


def pg_complementaries_plate(b):
    p = new_page(b, "complementaries", chapter=CH2)
    y = section_head(p, 96, "2.5 · COMPLEMENTARIES", "Opposites on the wheel",
                     lead="Each colour's complement is both its afterimage and its "
                          "loudest neighbour.")
    cx, cy, r = W / 2, y + 250, 170
    draw_wheel(p, cx, cy, r, inner=0.58)
    pairs = [(0, 6), (2, 8), (4, 10)]
    for a_i, b_i in pairs:
        aa = math.radians(a_i * 30 + 15)
        bb = math.radians(b_i * 30 + 15)
        p.line([cx + r * 0.62 * math.sin(aa), cy - r * 0.62 * math.cos(aa)],
               [cx + r * 0.62 * math.sin(bb), cy - r * 0.62 * math.cos(bb)],
               **stroke(2, color=SUB))
    yy = y + 470
    for i, (a_i, b_i) in enumerate(pairs):
        x = M + 30 + i * (CW - 60) / 3
        w2 = (CW - 60) / 3 - 24
        p.rect([x, yy, w2 / 2, 74], fill=WHEEL[a_i][1])
        p.rect([x + w2 / 2, yy, w2 / 2, 74], fill=WHEEL[b_i][1])
        p.text([x, yy + 82, w2, 12],
               f"{WHEEL[a_i][0].upper()} · {WHEEL[b_i][0].upper()}", style="labelC")
    caption(p, [M, yy + 120, CW, 40],
            "The three classical pairs — red/green, orange/blue, yellow/violet. Each "
            "pair mixed as pigment yields near-grey: they exhaust each other.")
    p.text([M, yy + 176, CW, 110],
           "Complementaries are colour's strongest chord and its most dangerous. At "
           "full strength and equal area they vibrate — each inflames the other, per "
           "the law. The old masters' remedy, which Chevreul endorses: unbalance them. "
           "Let one rule and the other visit, subdued in tone or confined to a line.",
           style="body")


def pg_harmonies_table(b):
    p = new_page(b, "harmonies", chapter=CH2)
    y = section_head(p, 96, "2.6 · THE SIX HARMONIES", "A grammar of agreement",
                     lead="From one law, Chevreul derived every way colours can "
                          "please together — six, in two families.")
    framed_panel(p, [M, y + 6, CW, 560])
    rows = [
        ("HARMONIES OF ANALOGY", None, None),
        ("1 · of scale", "tones of one scale, read in order",
         [tone(hue("Blue"), t) for t in (-0.7, -0.45, -0.2, 0.0, 0.3)]),
        ("2 · of hues", "like tones of neighbouring scales",
         [tone(hue("Blue-green"), -0.3), tone(hue("Blue"), -0.3),
          tone(hue("Blue-violet"), -0.3), tone(hue("Violet"), -0.3),
          tone(hue("Green"), -0.3)]),
        ("3 · of a dominant light", "many colours seen through one tint",
         [mix(h, "#e6c56a", 0.42) for h in (hue("Red"), hue("Green"), hue("Blue"),
                                            hue("Violet"), hue("Orange"))]),
        ("HARMONIES OF CONTRAST", None, None),
        ("4 · of scale", "two distant tones of one scale",
         [tone(hue("Red"), -0.75), tone(hue("Red"), -0.75), tone(hue("Red"), 0.45),
          tone(hue("Red"), -0.75), tone(hue("Red"), 0.45)]),
        ("5 · of hues", "unlike tones of neighbouring scales",
         [tone(hue("Yellow-green"), -0.55), tone(hue("Blue-green"), 0.35),
          tone(hue("Yellow-green"), -0.55), tone(hue("Blue-green"), 0.35),
          tone(hue("Yellow-green"), -0.55)]),
        ("6 · of colours", "distant scales, assorted by the law",
         [hue("Red"), hue("Green"), hue("Red"), hue("Green"), hue("Red")]),
    ]
    yy = y + 36
    for nm, d, chips in rows:
        if chips is None:
            p.text([M + 30, yy, 400, 15], nm, style="h3r")
            yy += 40
            continue
        p.text([M + 30, yy, 170, 34], nm, style="label")
        p.text([M + 30, yy + 20, 190, 40], d, style="cap")
        for i, c in enumerate(chips):
            p.rect([M + 250 + i * 68, yy - 4, 64, 44], fill=c)
        yy += 74
    caption(p, [M, y + 592, CW, 54],
            "Chevreul's taxonomy, each species shown as five chips. Analogy soothes; "
            "contrast wakes. The six plates that follow give each its own page.")
    p.text([M, y + 672, CW, 70],
           "The families divide by risk. The analogies are nearly foolproof — their "
           "failures are dullness. The contrasts are strong medicine — their failures "
           "are noise. Every workable palette is a treaty between the two.",
           style="body")


def _harmony_page(b, pid, *, num, family, name, definition, note, painter):
    p = new_page(b, pid, chapter=CH2)
    p.text([M, 90, CW, 14], f"HARMONY {num} · {family}", style="kicker")
    p.text([M, 118, CW, 44], name, style="h2")
    framed_panel(p, [M, 190, CW, 470])
    painter(p, M + 30, 220, CW - 60, 410)
    caption(p, [M, 686, CW, 54], definition)
    p.text([M, 766, CW, 150], note, style="body")
    return p


def pg_harm_scale(b):
    def paint(p, x, y, w, h):
        blues = [tone(hue("Blue"), t) for t in (-0.78, -0.55, -0.32, -0.1, 0.12, 0.38)]
        bh = h / len(blues)
        for i, c in enumerate(blues):
            p.rect([x, y + i * bh, w, bh + 0.5], fill=c)
        p.text([x + 24, y + 24, 340, 30], "one scale, read in order",
               style={"class": "h3", "color": "#1c3a57"})
    _harmony_page(
        b, "harm-scale", num="1 OF 6", family="ANALOGY", name="Harmony of scale",
        definition="“The harmony of scale, produced by the simultaneous view of "
                   "different tones of the same scale, more or less approximating.” "
                   "One colour, stepped in tone.",
        note="This is the safest harmony in the book — a monochrome. Its music is "
             "entirely rhythm: the intervals between tones. Keep them even (Chevreul "
             "specifies “equal and sufficiently numerous intervals”) and the "
             "scale reads as one calm material; skip unevenly and it reads as a "
             "mistake in the printing. It is the harmony of engravings, of washes, "
             "of interfaces in ‘dark mode’ — and its failure mode is not ugliness "
             "but sleep.",
        painter=paint)


def pg_harm_hues(b):
    def paint(p, x, y, w, h):
        fams = ["Yellow-green", "Green", "Blue-green", "Blue", "Blue-violet"]
        bw = w / len(fams)
        for i, f in enumerate(fams):
            p.rect([x + i * bw, y, bw + 0.5, h], fill=tone(hue(f), -0.22))
        p.text([x + 24, y + 24, 420, 30], "neighbouring scales, one depth",
               style={"class": "h3", "color": "#f4efe2"})
    _harmony_page(
        b, "harm-hues", num="2 OF 6", family="ANALOGY", name="Harmony of hues",
        definition="“The harmony of hues, produced by the simultaneous view of "
                   "tones of the same, or nearly the same depth, belonging to "
                   "neighbouring scales.” Adjacent families, matched in tone.",
        note="The analogous palette: a walk along the wheel without crossing it. "
             "Because the scales share pigment, they agree; because they differ in "
             "hue, they breathe. The discipline is the matched depth — let one "
             "neighbour go much darker and you have wandered into a contrast of "
             "hues (plate 5) without deciding to. Nature works this harmony "
             "constantly: foliage is its permanent exhibition.",
        painter=paint)


def pg_harm_dominant(b):
    def paint(p, x, y, w, h):
        cols = [hue("Red"), hue("Orange"), hue("Green"), hue("Blue"), hue("Violet"),
                hue("Red-violet")]
        bw = w / len(cols)
        for i, c in enumerate(cols):
            p.rect([x + i * bw, y, bw + 0.5, h * 0.44], fill=c)
            p.rect([x + i * bw, y + h * 0.56, bw + 0.5, h * 0.44],
                   fill=mix(c, "#e6c56a", 0.5))
        p.text([x, y + h * 0.455, w, 14],
               "THE SAME SIX — THEN SEEN THROUGH ONE AMBER LIGHT", style="labelC")
    _harmony_page(
        b, "harm-dominant", num="3 OF 6", family="ANALOGY",
        name="Harmony of a dominant light",
        definition="“The harmony of a dominant coloured light … various colours "
                   "assorted according to the law of contrast, but one of them "
                   "predominating, as would result from the view of these colours "
                   "through a slightly-coloured glass.”",
        note="The most painterly of the six: unity imposed not by choosing kindred "
             "colours but by bathing unkindred ones in a single tint. Every sunset "
             "performs it; every colour-grade in cinema is it, industrialised. In "
             "design it is the tinted overlay, the sepia archive, the brand wash — "
             "the cheapest unity money can buy, and legitimate whenever the "
             "colours underneath must stay recognisable rather than exact.",
        painter=paint)


def pg_contr_scale(b):
    def paint(p, x, y, w, h):
        deep = tone(hue("Red"), 0.42)
        pale = tone(hue("Red"), -0.78)
        p.rect([x, y, w, h], fill=pale)
        p.rect([x + w * 0.6, y, w * 0.4, h], fill=deep)
        p.text([x + 24, y + 24, 380, 30], "two tones, far apart, one scale",
               style={"class": "h3", "color": tone(hue('Red'), 0.55)})
    _harmony_page(
        b, "contr-scale", num="4 OF 6", family="CONTRAST",
        name="Harmony of contrast of scale",
        definition="“The harmony of contrast of scale, produced by the simultaneous "
                   "view of two very distant tones of the same scale.” The "
                   "monochrome, awake.",
        note="Take the harmony of scale (plate 29) and remove the middle. What remains is the simplest "
             "strong statement in colour: one family, two registers. Because hue is "
             "constant, the contrast is pure tone — which is why it survives every "
             "test this book applies later: it greys well, it prints well, it reads "
             "to every kind of colour vision. When in doubt, this is the harmony "
             "to reach for.",
        painter=paint)


def pg_contr_hues(b):
    def paint(p, x, y, w, h):
        a = tone(hue("Yellow-green"), -0.6)
        bb = tone(hue("Blue-green"), 0.3)
        bw = w / 7
        for i in range(7):
            p.rect([x + i * bw, y, bw + 0.5, h], fill=(a if i % 2 == 0 else bb))
        p.text([x + 24, y + 24, 400, 30], "neighbours, unequal in depth",
               style={"class": "h3", "color": "#f4efe2"})
    _harmony_page(
        b, "contr-hues", num="5 OF 6", family="CONTRAST",
        name="Harmony of contrast of hues",
        definition="“The harmony of contrast of hues, produced by the simultaneous "
                   "view of tones of different depths, belonging to neighbouring "
                   "scales.” Kinship in hue, opposition in tone.",
        note="The subtlest of the contrasts: the scales are still neighbours, so "
             "nothing clashes, but the depths disagree, so nothing sleeps. Chevreul "
             "warns that assortments of allied scales are “very difficult to "
             "obtain” at equal strength — and gives the painter's escape: "
             "“sacrifice one of the colours, which he subdues, to make the other "
             "more brilliant.” Subordination, not balance, is the trick.",
        painter=paint)


def pg_contr_colours(b):
    def paint(p, x, y, w, h):
        red, grn = hue("Red"), hue("Green")
        p.rect([x, y, w, h], fill=tone(grn, -0.68))
        p.rect([x, y + h * 0.30, w, h * 0.4], fill=grn)
        p.circle([x + w * 0.5, y + h * 0.5], h * 0.16, fill=red)
        p.text([x + 24, y + 24, 440, 30], "distant scales — the complementaries",
               style={"class": "h3", "color": tone(grn, 0.5)})
    _harmony_page(
        b, "contr-colours", num="6 OF 6", family="CONTRAST",
        name="Harmony of contrast of colours",
        definition="“The harmony of contrast of colours, produced by the "
                   "simultaneous view of colours belonging to very distant scales, "
                   "assorted according to the law of contrast.” The full chord.",
        note="The loudest harmony, and the one the law most directly underwrites: "
             "complementaries make each other “as dissimilar as possible,” so "
             "each burns brightest at the other's side. Chevreul found the view of "
             "complementary pairs “an undoubtedly agreeable sensation” — at the "
             "right dose. The plate shows the dose: one colour as field, the other "
             "as event. Reverse the areas and the page shouts.",
        painter=paint)


def pg_wbg(b):
    p = new_page(b, "wbg", chapter=CH2)
    y = section_head(p, 96, "2.7 · THE NEUTRALS", "Colours with white, black & grey",
                     lead="Chevreul tested every colour against the three silent "
                          "companions. The findings still govern every page.")
    framed_panel(p, [M, y + 6, CW, 470])
    hues6 = ["Red", "Orange", "Yellow", "Green", "Blue", "Violet"]
    cols = [("WITH WHITE", "#ffffff"), ("WITH BLACK", "#17130e"),
            ("WITH GREY", "#8a8378")]
    cw3 = (CW - 60 - 130) / 3
    for j, (nm, _) in enumerate(cols):
        p.text([M + 160 + j * cw3, y + 30, cw3, 12], nm, style="labelC")
    yy = y + 56
    for hn in hues6:
        p.text([M + 30, yy + 18, 120, 12], hn.upper(), style="label")
        for j, (_, ground) in enumerate(cols):
            x = M + 160 + j * cw3
            p.rect([x, yy, cw3 - 14, 58], fill=ground)
            p.rect([x + (cw3 - 14) / 2 - 21, yy + 12, 42, 34], fill=hue(hn))
        yy += 66
    caption(p, [M, y + 500, CW, 54],
            "Six hues on the three neutrals. White lends every colour depth (the "
            "eye darkens it by contrast of tone); black lends brilliance and eats "
            "the deep tones; grey flatters everything and competes with nothing.")
    p.text([M, y + 580, CW, 120],
           "Chevreul's practical summary survives translation to every medium: white "
           "grounds suit the light tones, black grounds the brilliant ones, grey "
           "grounds whatever must be judged precisely — which is why proofing rooms "
           "and galleries are painted in it. The page you are reading applies the "
           "first rule; the chapter openers apply the second.",
           style="body")


def pg_value(b):
    p = new_page(b, "value", chapter=CH2)
    y = section_head(p, 96, "2.8 · VALUE", "Value does the work",
                     lead="Strip the hue from a design and its skeleton of light "
                          "and dark remains — or is revealed to be missing.")
    framed_panel(p, [M, y + 6, CW, 520])
    # good composition: strong tone structure
    def comp(p, x, y0, w, h, cols):
        p.rect([x, y0, w, h], fill=cols[0])
        p.rect([x, y0, w, h * 0.32], fill=cols[1])
        p.rect([x + w * 0.08, y0 + h * 0.44, w * 0.5, h * 0.14], fill=cols[2])
        p.rect([x + w * 0.08, y0 + h * 0.66, w * 0.36, h * 0.1], fill=cols[3])
        p.circle([x + w * 0.78, y0 + h * 0.62], h * 0.14, fill=cols[4])
    good = [tone(hue("Blue"), -0.82), tone(hue("Blue"), 0.42), hue("Orange"),
            tone(hue("Blue"), -0.35), hue("Red")]
    bad = [tone(hue("Green"), -0.1), hue("Red"), hue("Blue"),
           hue("Orange"), hue("Violet")]
    hw = (CW - 90) / 2
    comp(p, M + 30, y + 40, hw, 200, good)
    comp(p, M + 60 + hw, y + 40, hw, 200, bad)
    comp(p, M + 30, y + 280, hw, 200, [luma_grey(c) for c in good])
    comp(p, M + 60 + hw, y + 280, hw, 200, [luma_grey(c) for c in bad])
    p.text([M + 30, y + 246, hw, 13], "TONES PLANNED, HUES ADDED", style="labelC")
    p.text([M + 60 + hw, y + 246, hw, 13], "HUES ONLY, TONES FORGOTTEN", style="labelC")
    p.text([M + 30, y + 486, hw, 13], "— SURVIVES THE GREY TEST —", style="labelC")
    p.text([M + 60 + hw, y + 486, hw, 13], "— DISSOLVES INTO ONE MASS —", style="labelC")
    caption(p, [M, y + 550, CW, 54],
            "The same two layouts, drained of hue. The left one was designed in "
            "tones first (a contrast of scale, plate 32) and keeps its hierarchy; "
            "the right one trusted hue alone, and the grey test dissolves it.")
    p.text([M, y + 630, CW, 80],
           "The test is Chevreul's vocabulary made procedure: tone is the axis the "
           "eye trusts first, in dim light, at distance, in poor print, and in the "
           "several percent of readers for whom hue is unreliable ground.",
           style="body")


def pg_type_grounds(b):
    p = new_page(b, "type-grounds", chapter=CH2)
    y = section_head(p, 96, "2.9 · COLOUR MEETS THE LETTER", "Text on grounds",
                     lead="Here the book's two halves shake hands: the letter is a "
                          "figure, and every figure obeys the law of its ground.")
    framed_panel(p, [M, y + 6, CW, 560])
    demo = "The quick brown fox jumps over the lazy dog"
    rows = [
        (INK, PAPER, "INK ON PAPER — the thousand-year default", "GOOD"),
        (CREAM, NIGHT, "PAPER ON NIGHT — fine; letters look a half-weight bolder", "GOOD"),
        (RUBRIC, PAPER, "RUBRIC ON PAPER — emphasis with a pedigree", "GOOD"),
        ("#9a9080", PANEL, "GREY ON GREY — a contrast of scale, too close", "WEAK"),
        (hue("Yellow"), "#ffffff", "YELLOW ON WHITE — like tones, no contrast", "FAILS"),
        (hue("Red"), hue("Green"), "RED ON GREEN — full hue contrast, near-equal "
                                   "tone: the letters vibrate", "FAILS"),
    ]
    yy = y + 36
    for fg, bg, nt, verdict in rows:
        p.rect([M + 30, yy, CW - 220, 62], fill=bg)
        p.text([M + 48, yy + 16, CW - 250, 34], demo,
               style={"font_family": SERIF, "font_size": 16.5, "color": fg})
        vcol = {"GOOD": "#3a7a3f", "WEAK": GOLD, "FAILS": RUBRIC}[verdict]
        p.text([M + CW - 170, yy + 10, 140, 14],
               f"{verdict} · {wcag_ratio(fg, bg):.1f}:1",
               style={"class": "label", "color": vcol})
        p.text([M + CW - 170, yy + 28, 150, 26], nt.split(" — ")[0],
               style={"class": "cap", "font_size": 8.5})
        yy += 84
    caption(p, [M, y + 592, CW, 54],
            "Six settings of one sentence, each with its measured contrast ratio. "
            "Legibility is tone contrast between figure and ground — modern "
            "practice asks at least 4.5:1 for body text (WCAG) — while hue "
            "contrast alone (the last row) adds vibration but no legibility.")
    p.text([M, y + 668, CW, 66],
           "Rule of craft: choose text and ground as tones first — dark on light or "
           "light on dark, well separated in the scale — and only then admit hue.",
           style="body")


def pg_rubric(b):
    p = new_page(b, "rubric", chapter=CH2)
    y = section_head(p, 96, "2.10 · THE SECOND VOICE", "Red, the working accent",
                     lead="For a thousand years the book had exactly two colours "
                          "of ink — and needed no third.")
    framed_panel(p, [M, y + 6, CW, 430])
    # a rubricated manuscript page in miniature
    px, py, pw2, ph2 = M + 40, y + 40, CW - 320, 360
    p.rect([px, py, pw2, ph2], fill="#fffdf6")
    p.rect([px, py, pw2, ph2], fill="none", **stroke(1, color=HAIR))
    p.text([px + 24, py + 20, pw2 - 48, 16], "INCIPIT LIBER SECUNDUS",
           style={"class": "labelR"})
    p.text([px + 24, py + 44, 60, 80], "Q",
           style={"font_family": SERIF, "font_size": 64, "color": RUBRIC})
    p.text([px + 88, py + 52, pw2 - 116, 60],
           "uietly the black text carries the argument, line after line,",
           style={"font_family": SERIF, "font_size": 13.5, "color": INK,
                  "line_height": 1.5})
    for li in range(6):
        p.rect([px + 24, py + 132 + li * 22, (pw2 - 48) * (0.98 - 0.07 * (li % 3)), 8],
               fill=mix(INK, "#ffffff", 0.25))
    p.text([px + 24, py + 286, pw2 - 48, 20], "Nota bene —",
           style={"font_family": SERIF, "font_size": 14, "color": RUBRIC,
                  "italic": True})
    for li in range(2):
        p.rect([px + 24, py + 314 + li * 20, (pw2 - 48) * 0.86, 8],
               fill=mix(INK, "#ffffff", 0.25))
    # annotations
    ax = px + pw2 + 26
    notes = [(py + 30, "the rubric: a heading in red — the word is the practice"),
             (py + 70, "the versal: one red letter buys the eye's entry"),
             (py + 292, "the red side-note: emphasis without shouting")]
    for ny, nt in notes:
        p.line([px + pw2 - 6, ny + 6], [ax - 8, ny + 6], **stroke(1, color=RUBRIC))
        p.text([ax, ny - 4, 190, 60], nt, style="cap")
    caption(p, [M, y + 462, CW, 54],
            "The rubricator's system, drawn small. Johnston: “a page of red (or "
            "blue) writing is very pleasant and effective, [and] we may certainly "
            "take advantage of such a reasonable excuse for introducing it.”")
    p.text([M, y + 542, CW, 130],
           "The manuscript tradition is a complete theory of accent colour. One "
           "hue, reserved for structure — headings, initials, emphasis — never for "
           "the text itself; used sparingly enough that its every appearance is "
           "information. “Italics — either in black or red — go best with Roman "
           "characters,” Johnston adds: the accent may be a colour or a voice, "
           "but it is one accent. This book's red follows the rule it describes.",
           style="body")


def pg_palette(b):
    p = new_page(b, "palette", chapter=CH2)
    y = section_head(p, 96, "2.11 · THE CLOSED PALETTE", "A recipe that holds",
                     lead="Everything in this chapter compresses into one "
                          "buildable object: a small palette with assigned duties.")
    framed_panel(p, [M, y + 6, CW, 480])
    # proportion bar
    bar_y = y + 40
    segs = [(PAPER, 0.62, "GROUND"), (INK, 0.30, "TEXT & STRUCTURE"),
            (RUBRIC, 0.08, "ACCENT")]
    x = M + 30
    for c, frac, nm in segs:
        wseg = (CW - 60) * frac
        p.rect([x, bar_y, wseg, 84], fill=c)
        p.rect([x, bar_y, wseg, 84], fill="none", **stroke(1, color=HAIR))
        p.text([x + 10, bar_y + 92, wseg + 60, 12], f"{nm} · {int(frac * 100)}%",
               style="label")
        x += wseg
    # roles
    yy = bar_y + 140
    roles = [
        ("THE GROUND", PAPER, "one paper, warm or cool — never two"),
        ("THE INK", INK, "one near-black; pure #000 is harsher than any ink ever was"),
        ("THE ACCENT", RUBRIC, "one hue with one duty (here: structure & emphasis)"),
        ("THE QUIET STEPS", "#8a8378", "greys from the ink's own scale, for the "
                                       "second rank"),
    ]
    for nm, c, d in roles:
        p.rect([M + 30, yy, 44, 44], fill=c)
        p.rect([M + 30, yy, 44, 44], fill="none", **stroke(1, color=HAIR))
        p.text([M + 94, yy + 2, 170, 14], nm, style="label")
        p.text([M + 94, yy + 20, CW - 160, 26], d, style="cap")
        yy += 62
    p.text([M + 30, yy + 8, CW - 100, 18],
           "EXPANSION RULE — NEED A SECOND ACCENT? TAKE THE FIRST ACCENT'S",
           style={"class": "labelR", "font_size": 9.5, "letter_spacing": 1.2})
    p.text([M + 30, yy + 26, CW - 100, 18],
           "COMPLEMENT, SUBDUED (PLATE 34), OR A DISTANT TONE OF THE INK (PLATE 32).",
           style={"class": "labelR", "font_size": 9.5, "letter_spacing": 1.2})
    caption(p, [M, y + 512, CW, 54],
            "A closed palette with assigned duties. Every choice traces to a plate "
            "in this chapter; nothing is decoration.")
    p.text([M, y + 592, CW, 100],
           "‘Closed’ is the operative word. A palette is a type family for colour: "
           "its value is not the beauty of its members but the impossibility of "
           "strangers. When a colour outside the set appears, it should mean "
           "something — exactly as an italic does.",
           style="body")


def pg_hue_alone(b):
    p = new_page(b, "hue-alone", chapter=CH2)
    y = section_head(p, 96, "2.12 · A LAST CAUTION", "Never hue alone",
                     lead="Colour is the strongest signal on the page and the "
                          "least reliable channel in the audience.")
    framed_panel(p, [M, y + 6, CW, 460])
    ranks = [0.95, 0.8, 0.62, 0.45, 0.3, 0.18]
    fams = ["Red", "Orange", "Yellow", "Green", "Blue", "Violet"]
    bw = (CW - 120) / 6
    # top: hue-only encoding
    p.text([M + 30, y + 30, 400, 13], "RANK ENCODED BY HUE ALONE", style="labelR")
    base_y = y + 200
    for i, (f, r) in enumerate(zip(fams, ranks)):
        x = M + 60 + i * bw
        p.rect([x, base_y - r * 130, bw - 16, r * 130], fill=hue(f))
    p.text([M + 30, base_y + 10, CW - 60, 42],
           "which bar is third? the wheel has no order — and for protan readers "
           "the red–green distinction survives only as a difference of tone",
           style="cap")
    # bottom: redundant encoding
    p.text([M + 30, base_y + 60, 400, 13], "RANK ENCODED THREE WAYS", style="labelR")
    base2 = y + 420
    for i, r in enumerate(ranks):
        x = M + 60 + i * bw
        p.rect([x, base2 - r * 130, bw - 16, r * 130], fill=tone(hue("Blue"),
                                                                 0.5 - i * 0.22))
        p.text([x, base2 - r * 130 - 18, bw - 16, 14], str(i + 1),
               style={"class": "labelC", "color": SUB})
    p.text([M + 30, base2 + 10, CW - 60, 42],
           "one scale ordered by tone, position ordered by rank, and the rank "
           "printed — colour now decorates a message that no longer depends on it",
           style="cap")
    caption(p, [M, y + 492, CW, 54],
            "The same six values twice. Hue has no natural order and not every eye "
            "receives it; tone has both. Encode with tone, position and label — "
            "then let hue sing over a structure that stands without it.")
    p.text([M, y + 570, CW, 116],
           "This is not a plea for beige. It is Chevreul's own division of labour: "
           "hue for character, tone for structure. The page that observes it loses "
           "nothing when photocopied, projected badly, or read by the one reader in "
           "twelve — roughly one man in twelve — whose red and green are neighbours (see Sources).",
           style="body")


def pg_ch2_close(b):
    p = new_page(b, "ch2-close", chrome=False, folio=True)
    p.text([M, 170, CW, 16], "CHAPTER II · A SUMMARY IN FIVE LINES", style="kickerC")
    lines = [
        "Tone, scale and hue are three different decisions — name them apart.",
        "No colour is seen alone: every neighbour pushes toward its opposite.",
        "Analogy soothes and risks sleep; contrast wakes and risks noise.",
        "Plan in tones, then admit hue; the grey test keeps everyone honest.",
        "Close the palette and give each colour a duty — accent is information.",
    ]
    y = 280
    for i, ln in enumerate(lines):
        p.text([M + 40, y, 40, 40], ["I", "II", "III", "IV", "V"][i], style="h3r")
        p.text([M + 110, y - 4, CW - 150, 60], ln, style="lead")
        y += 92
    p.rect([W / 2 - 30, y + 30, 60, 2], fill="rubric")
    bw = 200 / 12
    for i, (_, hx) in enumerate(WHEEL):
        p.rect([W / 2 - 100 + i * bw, y + 70, bw - 2, 22], fill=hx)
    p.text([0, y + 130, W, 14], "THE EYE COMPLETES EVERY PAGE WE LEAVE IT",
           style="attrC")


# --------------------------------------------------------------------------- #
# END MATTER (pp. 59-60)                                                       #
# --------------------------------------------------------------------------- #

def pg_sources(b):
    p = new_page(b, "sources", chrome=False, folio=True)
    p.text([M, 100, CW, 20], "SOURCES & FURTHER READING", style="kicker")
    p.text([M, 138, CW, 60], "Sources", style="h1")
    p.rect([M, 212, 64, 2], fill="rubric")
    entries = [
        ("Edward Johnston", "Writing & Illuminating, & Lettering", 84,
         "John Hogg, London, 1906 (read in the 1906 London printing). The source "
         "of Chapter I: the essential forms and the pen demonstrations (ch. XIV), "
         "the nine qualities (ch. XIV), the capital proportions (ch. XV), spacing "
         "(ch. IV), the margin canon (ch. VI), and the stone-cutting appendix by "
         "A. E. R. Gill. All Chapter I quotations are from this work."),
        ("M. E. Chevreul", "De la loi du contraste simultané des couleurs", 128,
         "Pitois-Levrault, Paris, 1839. Read here in two English renderings: "
         "The Laws of Contrast of Colour (London) and the fuller Principles of "
         "Harmony and Contrast of Colours (Martel line, 1854). Source of Chapter "
         "II — the definitions (§§122–127), the law (Part I, ch. II), the six "
         "harmonies — and of Chapter IV: the applications to tapestry, "
         "interiors, gardens, dress, and printing with types on coloured paper "
         "(Third Division)."),
        ("Ernest A. Batchelder", "The Principles of Design", 64,
         "The Inland Printer Company, Chicago, 1904 (2nd ed. 1906; printed "
         "uncopyrighted). The source of Chapter III: the terms and principles of "
         "pure design after Denman W. Ross's Harvard theory — tones, measures, "
         "shapes; rhythm, balance, harmony — and the balance demonstrations."),
        ("The Trajan Column inscription", "Rome, c. 113", 30,
         "The monumental model behind §1.7 — the capital proportions."),
        ("T. J. Cobden-Sanderson", "The Book Beautiful", 44,
         "Doves Press, London, 1902. The 'whole duty of typography' passage "
         "quoted on the Character page, as Johnston quotes it."),
    ]
    y = 254
    for author, title, note_h, note in entries:
        p.text([M, y, CW, 18], author, style="h3")
        p.text([M, y + 26, CW, 20], title,
               style={"font_family": SERIF, "font_size": 15.5, "color": INK,
                      "italic": True})
        p.text([M, y + 52, CW, note_h], note, style="bodyS")
        y += 52 + note_h + 26



def pg_plate_list(b):
    p = new_page(b, "plate-list", chrome=False, folio=True)
    p.text([M, 96, CW, 20], "LIST OF PLATES", style="kicker")
    p.text([M, 134, CW, 60], "The plates", style="h1")
    p.rect([M, 208, 64, 2], fill="rubric")
    colw = (CW - 24) / 2
    per_col = (len(PLATES) + 1) // 2
    pitch = 24 if per_col > 28 else 26
    for i, (num, title, page_n) in enumerate(PLATES):
        cx = M + (i // per_col) * (colw + 24)
        cy = 248 + (i % per_col) * pitch
        p.text([cx, cy, 30, 13], f"{num}", style="tocn")
        p.text([cx + 40, cy - 1, colw - 96, 22], title,
               style={"class": "cap", "font_size": 9.5})
        p.text([cx + colw - 46, cy, 40, 13], str(page_n), style="tocn")
    yy = 248 + per_col * pitch + 14
    note(p, [M, yy, CW, 54],
         "Every plate is an original vector drawing: demonstrations devised for "
         "this book, or reconstructions after Johnston, Chevreul and Batchelder "
         "where the caption names them.")


def pg_specimen_sources(b):
    p = new_page(b, "specimen-sources", chrome=False, folio=True)
    p.text([M, 96, CW, 20], "SPECIMEN SOURCES & FURTHER READING", style="kicker")
    p.text([M, 134, CW, 60], "Apparatus", style="h1")
    p.rect([M, 208, 64, 2], fill="rubric")
    yy = 236
    p.text([M, yy, CW, 14], "SPECIMEN SOURCES", style="labelR")
    p.rect([M, yy + 22, CW, 1], fill="hair")
    faces = [
        ("EB Garamond", "Georg Duffner, 2011", "SIL Open Font License 1.1",
         "revival after Garamont"),
        ("GFS Didot", "Greek Font Society", "SIL Open Font License 1.1",
         "revival after Didot"),
        ("Inter", "Rasmus Andersson", "SIL Open Font License 1.1",
         "designed for screens"),
        ("Gillius ADF", "Arkandis Digital Foundry", "GPL with font exception",
         "after the Gill tradition"),
        ("Cabin", "Pablo Impallari", "SIL Open Font License 1.1",
         "humanist-geometric"),
        ("Fira Mono", "Carrois for Mozilla", "SIL Open Font License 1.1",
         "contemporary monospace"),
    ]
    ty = yy + 32
    for face, designer, lic, status in faces:
        p.text([M, ty, 130, 14], face, style="label")
        p.text([M + 138, ty + 1, 200, 14], designer,
               style={"class": "cap", "font_size": 9.5})
        p.text([M + 344, ty + 1, 160, 14], lic,
               style={"class": "cap", "font_size": 9.5})
        p.text([M + 506, ty + 1, CW - 506, 26], status,
               style={"class": "cap", "font_size": 9.5})
        ty += 26
    note(p, [M, ty + 18, CW, 40],
         "Specimens are modern, openly licensed faces standing for their "
         "historical classes — none is the original metal. Garamont is the "
         "punchcutter's own spelling; Garamond names the genre.")
    ty += 74
    p.text([M, ty, CW, 14], "FURTHER READING & MODERN CLAIMS", style="labelR")
    for i, ln in enumerate([
        "Robert Bringhurst, The Elements of Typographic Style — the modern "
        "classification (garalde, didone, grotesque) used in §1.8.",
        "National Eye Institute, facts on colour blindness — the prevalence of "
        "red–green colour-vision deficiency (roughly 8% of men) cited in §2.12.",
        "Timeline dates in plate 1 are summarised from Johnston's development "
        "chapters (chs. I–III) and Bringhurst's historical appendices.",
        "Contrast ratios in plate 37 and the 1839 re-measurement follow the "
        "WCAG 2.x relative-luminance formula, computed for this book.",
    ]):
        p.text([M, ty + 24 + i * 36, CW, 32], ln, style="cap")


def pg_back_cover(b):
    p = new_page(b, "back-cover", chrome=False, folio=False, bg="night")
    cx, cy, r = W / 2, 400, 120
    draw_motif(p, cx, cy, r)
    for i, ln in enumerate(["A letter is a skeleton given voice by a tool.",
                            "A colour is a note played by its neighbours.",
                            "An arrangement is a truce among attractions.",
                            "Everything else in design is dosage."]):
        p.text([120, 572 + i * 34, W - 240, 30], ln,
               style={"class": "leadC", "color": "#d8c9ae"})
    p.rect([W / 2 - 30, 740, 60, 2], fill="rubric")
    p.text([0, 780, W, 14], f"THE LETTER & THE HUE · FOUR CHAPTERS · {PLATES[-1][0] if PLATES else 66} PLATES · MMXXVI",
           style="attrC")


CH3_PAGES: list = []  # populated after the chapter functions are defined
CH4_PAGES: list = []  # populated after the chapter functions are defined

CH2_PAGES = [
    pg_ch2_opener, pg_vocab_text, pg_tone_scales_plate, pg_wheel_plate, pg_law_text,
    pg_simultaneous_plate, pg_successive_plate, pg_complementaries_plate,
    pg_harmonies_table, pg_harm_scale, pg_harm_hues, pg_harm_dominant,
    pg_contr_scale, pg_contr_hues, pg_contr_colours, pg_wbg, pg_value,
    pg_type_grounds, pg_rubric, pg_palette, pg_hue_alone, pg_ch2_close,
]


# --------------------------------------------------------------------------- #
# CHAPTER III — THE ARRANGEMENT (pp. 59-74)                                    #
# After Ernest A. Batchelder, The Principles of Design (1904), itself after    #
# Denman W. Ross. Quotation ordinals refer to the corpus edition.              #
# --------------------------------------------------------------------------- #

def pg_ch3_opener(b):
    p = new_page(b, "ch3-opener", chrome=False, folio=False, bg="night")
    p.text([M, 200, CW, 24], "CHAPTER III", style="kickerC")
    p.text([0, 240, W, 170], "III", style={"class": "cnum", "align": "center"})
    p.text([0, 430, W, 80], "The Arrangement",
           style={"class": "hcover", "font_size": 74, "color": "#f4ead6"})
    p.rect([W / 2 - 30, 560, 60, 2], fill="rubric")
    p.text([120, 610, W - 240, 90],
           "On tones, measures and shapes; on rhythm, balance and harmony; "
           "and on the page as a field of attractions held in repose.",
           style={"class": "leadC", "color": "#d8c9ae"})
    # opener motif: a balanced beam of spots
    by = 810
    p.line([W / 2 - 170, by], [W / 2 + 170, by], **stroke(2.5, color=CREAM))
    p.polygon([[W / 2 - 8, by + 26], [W / 2 + 8, by + 26], [W / 2, by + 4]],
              fill=RUBRIC)
    p.circle([W / 2 - 60, by - 26], 24, fill=CREAM)
    p.circle([W / 2 + 80, by - 14], 12, fill=CREAM)
    p.circle([W / 2 + 117, by - 16], 14, fill=CREAM)


def pg_third_craft(b):
    p = new_page(b, "third-craft", chapter=CH3)
    y = section_head(p, 96, "3.1 · THE THIRD CRAFT", "Pure design",
                     lead="Letters and colours are materials. Arrangement is what "
                          "is done with them — and it, too, has a grammar.")
    p.text([M, y, CW, 150],
           "The grammar came out of Harvard. Denman W. Ross, lecturing on the theory "
           "of design at the turn of the century, proposed that composition could be "
           "studied apart from any subject — as pure design. His student and "
           "colleague Ernest Batchelder carried the theory to working craftsmen "
           "through a printers' trade journal, The Inland Printer, and gathered the "
           "articles into a book in 1904. It is the most practical statement of the "
           "theory ever printed, and he refused even to copyright it.",
           style="body")
    quote_block(p, y + 172,
                "Pure design is the composition of tones, measures, shapes, for the "
                "sake of rhythm, balance, harmony, the principles of order and "
                "beauty.", None)
    p.text([M, y + 292, CW, 14],
           "BATCHELDER, THE PRINCIPLES OF DESIGN (1904), AFTER ROSS", style="attr")
    p.text([M, y + 336, CW, 150],
           "Everything in this chapter unpacks that sentence. Three terms — tones "
           "(light and dark), measures (large and small), shapes (this form and "
           "that) — are all a composition contains. Three principles — rhythm, "
           "balance, harmony — are all that can be done well with them. The scheme "
           "sounds austere; in use it is the opposite. It gives the designer what "
           "Johnston's table gave the letterer and Chevreul's law gave the "
           "colourist: a small set of names for what the eye already knows.",
           style="body")


def pg_terms_principles(b):
    p = new_page(b, "terms-principles", chapter=CH3)
    y = section_head(p, 96, "3.2 · THE SYSTEM", "Three terms, three principles")
    framed_panel(p, [M, y + 4, CW, 560])
    # 3x3 grid: rows = terms, cols = principles
    gx, gy = M + 130, y + 64
    cw2 = (CW - 160) / 3
    rh = 150
    prins = [("RHYTHM", "regular change"), ("BALANCE", "opposed attractions"),
             ("HARMONY", "likeness in accord")]
    terms = [("TONES", "light & dark"), ("MEASURES", "large & small"),
             ("SHAPES", "this form & that")]
    for j, (pn, pd) in enumerate(prins):
        p.text([gx + j * cw2, y + 24, cw2, 13], pn, style="labelRC")
        p.text([gx + j * cw2, y + 40, cw2, 12], pd, style="capC")
    for i, (tn, td) in enumerate(terms):
        p.text([M + 20, gy + i * rh + 56, 104, 13], tn, style="labelR")
        p.text([M + 20, gy + i * rh + 72, 104, 24], td, style="cap")
    # cell demos, drawn small
    for i in range(3):
        for j in range(3):
            cx = gx + j * cw2 + 12
            cy = gy + i * rh + 8
            w2, h2 = cw2 - 24, rh - 24
            p.rect([cx, cy, w2, h2], fill="#ffffff")
            p.rect([cx, cy, w2, h2], fill="none", **stroke(1, color=HAIR))
            if i == 0:  # tones
                seq = ([0.9, 0.35, 0.9, 0.35, 0.9] if j == 0 else
                       [0.75, 0.2] if j == 1 else [0.55, 0.45, 0.5])
                if j == 0:
                    for k, t in enumerate(seq):
                        p.rect([cx + 10 + k * (w2 - 20) / 5, cy + 14,
                                (w2 - 20) / 5 - 4, h2 - 28],
                               fill=mix(INK, "#ffffff", t))
                elif j == 1:
                    p.rect([cx + 10, cy + 14, (w2 - 20) * 0.7, h2 - 28],
                           fill=mix(INK, "#ffffff", 0.75))
                    p.rect([cx + 10 + (w2 - 20) * 0.72, cy + 14, (w2 - 20) * 0.28,
                            h2 - 28], fill=mix(INK, "#ffffff", 0.1))
                else:
                    for k, t in enumerate(seq):
                        p.circle([cx + w2 * (0.25 + 0.25 * k), cy + h2 / 2],
                                 h2 * 0.22, fill=mix(INK, "#ffffff", t))
            elif i == 1:  # measures
                if j == 0:
                    for k in range(5):
                        r = 5 + k * 3.5
                        p.circle([cx + 16 + k * (w2 - 32) / 4, cy + h2 / 2], r,
                                 fill=INK)
                elif j == 1:
                    p.circle([cx + w2 * 0.30, cy + h2 / 2], 17, fill=INK)
                    p.circle([cx + w2 * 0.78, cy + h2 / 2], 9, fill=INK)
                    p.line([cx + 10, cy + h2 - 10], [cx + w2 - 10, cy + h2 - 10],
                           **stroke(1, color=SUB))
                else:
                    for k in range(4):
                        p.circle([cx + 18 + k * (w2 - 36) / 3, cy + h2 / 2], 11,
                                 fill=INK)
            else:  # shapes
                if j == 0:
                    for k in range(4):
                        xx = cx + 14 + k * (w2 - 28) / 3.5
                        if k % 2 == 0:
                            p.regular_polygon([xx, cy + h2 / 2], 11, 3, fill=INK)
                        else:
                            p.circle([xx, cy + h2 / 2], 9, fill=INK)
                elif j == 1:
                    p.regular_polygon([cx + w2 * 0.3, cy + h2 / 2], 15, 3, fill=INK)
                    p.rect([cx + w2 * 0.68 - 9, cy + h2 / 2 - 9, 18, 18], fill=INK)
                else:
                    for k in range(3):
                        p.regular_polygon([cx + w2 * (0.25 + 0.25 * k),
                                           cy + h2 / 2], 10 + k * 2, 6, fill=INK)
    caption(p, [M, y + 588, CW, 54],
            "The Ross system, drawn as a working grid — an author's reconstruction "
            "after Batchelder. Every cell is a composition of one term under one "
            "principle; every page you have ever admired is some weighting of the "
            "nine.")
    p.text([M, y + 664, CW, 104],
           "The system's whole ontology fits in one of his sentences: “a spot "
           "of paint may be described in three words; it is a tone, a measure, a "
           "shape.” Problems are “combinations of terms with principles.” "
           "The rest of the chapter works the grid.",
           style="body")


def pg_tone_balance_text(b):
    p = new_page(b, "tone-balance-1", chapter=CH3)
    y = section_head(p, 96, "3.3 · BALANCE OF TONE", "Repose, defined",
                     lead="“Balance means repose that results from the "
                          "opposition of attractions.”")
    p.text([M, y, CW, 128],
           "Everything on a page pulls at the eye: the darker, the larger, the more "
           "eccentric, the harder. Batchelder's definition makes balance a physical "
           "affair — not a look but an equilibrium. A composition is balanced when "
           "its attractions oppose one another so completely that the eye comes to "
           "rest; it is unbalanced when some pull goes unanswered.",
           style="body")
    p.text([M, y + 150, CW, 128],
           "Tone balance is the first case: “the result of a selection and "
           "arrangement of contrasts in such way that each part of a design may "
           "keep its proper place without being unduly emphasized at the expense of "
           "other parts.” Black is the heaviest material on a white page; a "
           "little of it outweighs a great deal of grey.",
           style="body")
    # small inline demo: same grey field, one black spot moves the weight
    yy = y + 306
    framed_panel(p, [M, yy, CW, 250])
    for k, (spot_x, verdict) in enumerate([(0.78, "UNANSWERED — the page lists"),
                                           (0.30, "OPPOSED — the page rests")]):
        bx = M + 30 + k * (CW - 60) / 2
        bw2 = (CW - 60) / 2 - 20
        p.rect([bx, yy + 24, bw2, 160], fill="#ffffff")
        p.rect([bx, yy + 24, bw2, 160], fill="none", **stroke(1, color=HAIR))
        p.rect([bx + bw2 * 0.08, yy + 44, bw2 * 0.38, 120],
               fill=mix(INK, "#ffffff", 0.72))
        p.circle([bx + bw2 * spot_x, yy + 104], 17, fill=INK)
        if k == 1:
            p.circle([bx + bw2 * 0.82, yy + 76, ], 7, fill=INK)
            p.circle([bx + bw2 * 0.86, yy + 136], 5, fill=INK)
        p.text([bx, yy + 196, bw2, 12], verdict, style="labelC")
    caption(p, [M, yy + 276, CW, 40],
            "One grey mass, one black spot. Alone, the spot drags the composition "
            "rightward; answered by two small blacks, the attractions oppose and "
            "the field comes to rest.")


def pg_tone_balance_plate(b):
    p = new_page(b, "tone-balance-2", chapter=CH3)
    framed_panel(p, [M, 90, CW, 620])
    # two full compositions: distribution of two tones
    for k, mode in enumerate(("massed", "scattered")):
        bx = M + 30 + k * (CW - 60) / 2
        bw2 = (CW - 60) / 2 - 20
        p.rect([bx, 130, bw2, 400], fill="#ffffff")
        p.rect([bx, 130, bw2, 400], fill="none", **stroke(1, color=HAIR))
        if mode == "massed":
            p.rect([bx + 20, 150, bw2 - 40, 150], fill=INK)
            p.rect([bx + 20, 320, bw2 * 0.5, 16], fill=INK)
            p.rect([bx + 20, 352, bw2 * 0.62, 16], fill=mix(INK, "#ffffff", 0.45))
            p.rect([bx + 20, 384, bw2 * 0.44, 16], fill=mix(INK, "#ffffff", 0.45))
            p.rect([bx + 20, 470, bw2 - 40, 40], fill=mix(INK, "#ffffff", 0.82))
            p.text([bx, 546, bw2, 12], "TONES MASSED — QUIET", style="labelC")
        else:
            import random as _r
            _r.seed(7)
            for i in range(26):
                xx = bx + 18 + _r.random() * (bw2 - 48)
                yy2 = 148 + _r.random() * 356
                if i % 2 == 0:
                    p.rect([xx, yy2, 14 + _r.random() * 22, 10], fill=INK)
                else:
                    p.rect([xx, yy2, 10 + _r.random() * 30, 8],
                           fill=mix(INK, "#ffffff", 0.5))
            p.text([bx, 546, bw2, 12], "THE SAME INK, SCATTERED — NOISE",
                   style="labelC")
    p.text([M + 30, 586, CW - 60, 90],
           "Black and grey, massed against scattered. Massed, the tones keep "
           "station and the design reads at a glance; scattered, every fragment "
           "is an unanswered attraction and the eye is pulled twenty-six ways.",
           style={"font_family": SERIF, "font_size": 14, "color": INK,
                  "line_height": 1.5})
    caption(p, [M, 736, CW, 40],
            "Tone balance as distribution — an author's demonstration on "
            "Batchelder's principle. Ink is weight; weight wants marshalling.")
    p.text([M, 812, CW, 110],
           "The typographic translation is immediate. A page's blacks are its "
           "display sizes and its bolds; its greys are the text; its whites, the "
           "margins. A layout that bolds everything scatters its ink — and a reader "
           "who cannot find the heaviest thing has been told nothing about where to "
           "begin.",
           style="body")


def pg_steelyard_text(b):
    p = new_page(b, "steelyard-1", chapter=CH3)
    y = section_head(p, 96, "3.4 · BALANCE OF MEASURE", "The steelyard",
                     lead="“The spots are to the eye precisely like physical "
                          "forces in nature, at least in so far as this law of "
                          "balance applies.”")
    p.text([M, y, CW, 168],
           "Batchelder's boldest move is to make balance computable, and his "
           "figure for it is a playground: two unequal spots “may be likened to "
           "a man and a boy on a ‘see-saw,’ the man weighing twice as much as the "
           "boy” — so “it would be necessary to give the boy two parts of "
           "the board and the man one.” The larger measure sits nearer the "
           "fulcrum, the smaller farther out, and their moments must agree; then "
           "“the point of balance of the measures shall coincide with the "
           "center of the frame,” for the eye seeks both at once.",
           style="body")
    p.text([M, y + 190, CW, 128],
           "This is not a metaphor; it is a procedure, and the plate opposite "
           "performs it with real numbers. Two discs are weighed by their areas; "
           "the balance point is computed as any physicist would compute it; and "
           "the pair is then set so that this point falls on the frame's centre. "
           "The repose you feel looking at the result is arithmetic, felt.",
           style="body")
    p.text([M, y + 340, CW, 90],
           "Asymmetric layout — the whole modern art of it — is this steelyard "
           "worked at speed: a large image close to the axis, a small caption far "
           "from it, and the page at rest without a single centred element.",
           style="body")


def pg_steelyard_plate(b):
    p = new_page(b, "steelyard-2", chapter=CH3)
    framed_panel(p, [M, 90, CW, 640])
    # Demo 1: equal measures
    cy1 = 200
    cx = M + CW / 2
    r_eq = 26
    d_eq = 150
    p.line([cx - d_eq, cy1], [cx + d_eq, cy1], **stroke(2, color=SUB))
    p.polygon([[cx - 7, cy1 + 22], [cx + 7, cy1 + 22], [cx, cy1 + 2]], fill=RUBRIC)
    p.circle([cx - d_eq, cy1 - r_eq - 4], r_eq, fill=INK)
    p.circle([cx + d_eq, cy1 - r_eq - 4], r_eq, fill=INK)
    for sgn in (-1, 1):
        p.line([cx, cy1 + 34], [cx + sgn * d_eq, cy1 + 34], **stroke(1, color=RUBRIC))
        p.text([cx + sgn * d_eq / 2 - 30, cy1 + 40, 60, 12], "d", style="labelC")
    p.text([M + 30, 130, 400, 13], "EQUAL MEASURES — EQUAL ARMS", style="labelR")
    # Demo 2: unequal measures, computed
    cy2 = 440
    r1, r2 = 34, 24
    a1, a2 = r1 ** 2, r2 ** 2         # attraction ~ area
    d1 = 90
    d2 = d1 * a1 / a2                 # moments agree: a1*d1 = a2*d2
    p.text([M + 30, 330, 480, 13],
           "UNEQUAL MEASURES — ARMS INVERSELY AS THE ATTRACTIONS", style="labelR")
    p.line([cx - d1 - 40, cy2], [cx + d2 + 40, cy2], **stroke(2, color=SUB))
    p.polygon([[cx - 7, cy2 + 22], [cx + 7, cy2 + 22], [cx, cy2 + 2]], fill=RUBRIC)
    p.circle([cx - d1, cy2 - r1 - 4], r1, fill=INK)
    p.circle([cx + d2, cy2 - r2 - 4], r2, fill=INK)
    p.line([cx, cy2 + 34], [cx - d1, cy2 + 34], **stroke(1, color=RUBRIC))
    p.line([cx, cy2 + 52], [cx + d2, cy2 + 52], **stroke(1, color=RUBRIC))
    p.text([cx - d1, cy2 + 38, 90, 12], f"d1 = {d1}", style="label")
    p.text([cx + d2 - 96, cy2 + 56, 96, 12], f"d2 = {d2:.1f}", style="label")
    p.text([M + 30, cy2 + 84, CW - 60, 40],
           f"attractions as areas -- a1 : a2 = {a1} : {a2} ; "
           f"d2 = d1 x {a1}/{a2} = {d2:.1f}. the moments agree: "
           f"{a1} x {d1} = {a1 * d1} = {a2} x {d2:.1f}.",
           style="mono")
    # Demo 3: the same pair placed in a frame, balance point on centre
    fy = 600
    fw, fh = 300, 104
    fx = M + (CW - fw) / 2
    p.rect([fx, fy, fw, fh], fill="#ffffff")
    p.rect([fx, fy, fw, fh], fill="none", **stroke(1.2, color=SUB))
    scale = 0.55
    bal_x = fx + fw / 2
    p.circle([bal_x - d1 * scale, fy + fh / 2], r1 * scale, fill=INK)
    p.circle([bal_x + d2 * scale, fy + fh / 2], r2 * scale, fill=INK)
    p.line([bal_x, fy + 6], [bal_x, fy + fh - 6], **stroke(1, color=RUBRIC))
    p.text([fx - 60, fy + fh + 10, fw + 120, 14],
           "the computed balance point, set on the frame's centre",
           style="capC")
    caption(p, [M, 756, CW, 54],
            "The steelyard, solved — Batchelder's demonstration (plates XXI–XXIII "
            "of his book) re-computed for this one. His concise law: unequal "
            "measures “balance … at distances that will be in inverse ratio to "
            "the measures.” The frame's centre is where the equation must come "
            "out.")
    p.text([M, 836, CW, 80],
           "Work the procedure once by hand and you will never again centre a "
           "layout out of helplessness. Centring is the special case where the "
           "two arms are equal; the general instrument is the beam.",
           style="body")


def pg_shape_balance_text(b):
    p = new_page(b, "shape-balance-1", chapter=CH3)
    y = section_head(p, 96, "3.5 · BALANCE OF SHAPE", "Symmetry and its escape",
                     lead="“Shape balance in design may be defined as symmetry "
                          "… shapes on one side are opposed by corresponding "
                          "shapes on the other side.”")
    p.text([M, y, CW, 150],
           "Symmetry is balance made obvious: fold the design on its axis and the "
           "attractions cancel pair by pair. Batchelder honours it — “no "
           "difficulty will be experienced in finding examples of shape balance in "
           "the work of all nations and periods” — and then teaches the escape "
           "from it, because measure balance offers “the same feeling of repose "
           "that is found in symmetry” without the stiffness.",
           style="body")
    p.text([M, y + 190, CW, 128],
           "The pairing is the deep lesson of the chapter. Symmetry and asymmetry "
           "are not rival styles but two solutions of one equation — the first "
           "solved by mirroring, the second by weighing. A title page centred on "
           "its axis and a modernist spread with its heavy image high and left are "
           "obeying the same law with different arithmetic.",
           style="body")
    p.text([M, y + 340, CW, 80],
           "The plate opposite sets the two solutions side by side, built from the "
           "same three shapes — then breaks one, so the eye can feel the equation "
           "fail.",
           style="body")


def pg_shape_balance_plate(b):
    p = new_page(b, "shape-balance-2", chapter=CH3)
    framed_panel(p, [M, 90, CW, 620])
    w3 = (CW - 100) / 3
    demos = ["SYMMETRIC — MIRRORED", "ASYMMETRIC — WEIGHED", "BROKEN — UNANSWERED"]
    for k, name in enumerate(demos):
        bx = M + 30 + k * (w3 + 20)
        p.rect([bx, 130, w3, 420], fill="#ffffff")
        p.rect([bx, 130, w3, 420], fill="none", **stroke(1, color=HAIR))
        cxk = bx + w3 / 2
        if k == 0:
            p.regular_polygon([cxk, 220], 34, 3, rotation=-90, fill=INK)
            p.circle([cxk - 44, 330], 20, fill=INK)
            p.circle([cxk + 44, 330], 20, fill=INK)
            p.rect([cxk - 52, 420, 104, 66], fill=mix(INK, "#ffffff", 0.55))
            p.line([cxk, 140, ], [cxk, 540], **stroke(1, color=RUBRIC))
        elif k == 1:
            p.rect([bx + 16, 170, w3 * 0.52, 200], fill=INK)
            p.circle([bx + w3 * 0.78, 300], 16, fill=INK)
            p.rect([bx + w3 * 0.66, 430, w3 * 0.26, 56],
                   fill=mix(INK, "#ffffff", 0.55))
            p.regular_polygon([bx + w3 * 0.30, 470], 14, 3, fill=INK)
        else:
            p.rect([bx + 16, 170, w3 * 0.52, 200], fill=INK)
            p.circle([bx + w3 * 0.30, 470], 8, fill=mix(INK, "#ffffff", 0.5))
        p.text([bx, 560, w3, 24], name, style="labelC")
    caption(p, [M, 736, CW, 54],
            "Three fields, one law. The mirrored field cancels its attractions in "
            "pairs; the weighed field cancels them on the beam; the third leaves "
            "its great black mass unanswered, and the whole page leans.")
    p.text([M, 812, CW, 100],
           "Cover the right-hand two-thirds of any spread you admire and guess "
           "where the answering weights must be; then look. The pleasure of a good "
           "asymmetric layout is the pleasure of a sum that comes out.",
           style="body")


def pg_rhythm_text(b):
    p = new_page(b, "rhythm-1", chapter=CH3)
    y = section_head(p, 96, "3.6 · RHYTHM", "Regular change",
                     lead="“Rhythm means joint action or movement … that enables "
                          "the eye to find a way through all the details of a "
                          "design.”")
    p.text([M, y, CW, 150],
           "The definition earns its length: rhythm is not decoration but "
           "navigation — the property that lets the eye find a way through. His "
           "image for it is a pond: “drop a stone into the water and as the "
           "waves grow larger and larger you have measure rhythm, a movement by "
           "means of interrelated measures.” A colonnade has it nobly; a "
           "well-set text page has it invisibly — the even fall of the baselines, "
           "the drumbeat of the word-spaces, the eye returned to the margin line "
           "after line like a carriage.",
           style="body")
    p.text([M, y + 190, CW, 128],
           "Two failures bound the principle. Repetition without change is "
           "monotony — the eye stops listening. Change without repetition is "
           "incident — the eye has nothing to learn. And rhythm cannot govern "
           "alone: “rhythm may enter into any arrangement; but rhythm without "
           "balance is a step toward ‘mob rule.’” Movement, in a composition "
           "as in a republic, wants a constitution.",
           style="body")
    p.text([M, y + 340, CW, 80],
           "The plate opposite drums the three cases: monotone, measured "
           "progression, and noise — the same marks in each.",
           style="body")


def pg_rhythm_plate(b):
    p = new_page(b, "rhythm-2", chapter=CH3)
    framed_panel(p, [M, 90, CW, 620])
    rows = [("MONOTONY — REPETITION WITHOUT CHANGE",
             [16] * 12, [1] * 12),
            ("RHYTHM — REPETITION WITH GOVERNED CHANGE",
             [12, 16, 22, 12, 16, 22, 12, 16, 22, 12, 16, 22], None),
            ("NOISE — CHANGE WITHOUT REPETITION",
             [8, 30, 11, 24, 6, 19, 33, 9, 15, 27, 7, 21], None)]
    y = 140
    for name, heights, _ in rows:
        p.text([M + 30, y, CW - 60, 13], name, style="labelR")
        bx = M + 40
        bw2 = (CW - 100) / 12
        for i, h in enumerate(heights):
            p.rect([bx + i * bw2 + bw2 * 0.2, y + 90 - h * 2, bw2 * 0.6, h * 2],
                   fill=INK)
        p.line([M + 40, y + 92], [M + CW - 60, y + 92], **stroke(1, color=HAIR))
        y += 160
    # baseline rhythm demo: three leadings on one staff
    p.text([M + 30, y, CW - 60, 13],
           "THE PAGE'S OWN DRUM — BASELINES AT ONE LEADING", style="labelR")
    for i in range(4):
        yy = y + 22 + i * 16
        p.rect([M + 40, yy, (CW - 120) * (0.94 - 0.06 * (i % 3)), 6],
               fill=mix(INK, "#ffffff", 0.35))
    caption(p, [M, 736, CW, 54],
            "Rhythm as governed change — an author's demonstration. The middle "
            "staff repeats a three-term motif (the modular scale in miniature); "
            "the last is the same ink with the government removed. Batchelder: "
            "measure rhythm “is to design what the crescendo and diminuendo "
            "are to music.”")
    p.text([M, 812, CW, 90],
           "Rhythm is also the reader's stamina. A page whose recurrences are "
           "learnable is a page the eye can walk through on habit, saving its "
           "attention for the sense — Johnston's readableness, arrived at from "
           "the other side.",
           style="body")


def pg_harmony_text(b):
    p = new_page(b, "harmony3-1", chapter=CH3)
    y = section_head(p, 96, "3.7 · HARMONY", "Likeness in accord",
                     lead="“Harmony means the consistency of likeness; having "
                          "something in common … a unity, all the terms of which "
                          "are in interior accord.”")
    p.text([M, y, CW, 150],
           "The third principle is the quietest, and the book you are holding has "
           "been arguing for it since page one. Johnston called it uniformity — the "
           "family likeness of letters. Chevreul called it harmony of scale and of "
           "hues — colours with pigment in common. Batchelder generalises: any "
           "composition holds together exactly in so far as its parts share "
           "something — a unit of measure, a family of shapes, a range of tones.",
           style="body")
    p.text([M, y + 190, CW, 128],
           "His practical instrument is the common unit: designs governed by "
           "“a common unit of measure, the quarter-inch of the squared "
           "paper.” Set every dimension as a multiple of one module and measure "
           "harmony is manufactured, not hoped for — the ancestor, four decades "
           "early, of the baseline grid and the 8-point spacing system.",
           style="body")
    p.text([M, y + 340, CW, 90],
           "The plate opposite turns the principle on this book itself: one of its "
           "own pages, weighed and measured by the system it has been teaching.",
           style="body")


def pg_book_weighed_plate(b):
    p = new_page(b, "book-weighed", chapter=CH3)
    framed_panel(p, [M, 90, CW, 640])
    # A miniature facsimile of plate 25's page (p42, simultaneous contrast),
    # rebuilt at scale from its real geometry, then annotated as pure design.
    s = 0.44
    fx, fy = M + 90, 128
    pw2, ph2 = W * s, H * s
    p.rect([fx, fy, pw2, ph2], fill=PAPER)
    p.rect([fx, fy, pw2, ph2], fill="none", **stroke(1.2, color=SUB))
    grounds = ["#ffffff", "#17130e", hue("Yellow"), hue("Blue"), hue("Red"),
               hue("Green")]
    GREY = "#8a8378"
    colw = (CW - 60) / 3
    masses = []  # (cx, cy, weight) for the tone-centroid computation
    for i, g in enumerate(grounds):
        x = (M + 30 + (i % 3) * colw) * s + fx
        yy = (130 + (i // 3) * 250) * s + fy
        wj, hj = (colw - 20) * s, 190 * s
        p.rect([x, yy, wj, hj], fill=g)
        p.rect([x + wj / 2 - 34 * s, yy + 61 * s, 68 * s, 68 * s], fill=GREY)
        lum = wcag_ratio("#ffffff", g)  # darker ground => higher ratio vs white
        weight = lum * wj * hj
        masses.append((x + wj / 2, yy + hj / 2, weight))
    # fluting strip
    for i in range(8):
        p.rect([(M + i * CW / 8) * s + fx, 810 * s + fy, (CW / 8) * s + 0.5,
                90 * s], fill=tone(hue("Blue"), -0.85 + i * 0.2))
        masses.append(((M + (i + 0.5) * CW / 8) * s + fx, (810 + 45) * s + fy,
                       wcag_ratio("#ffffff", tone(hue("Blue"), -0.85 + i * 0.2))
                       * (CW / 8) * 90 * s * s))
    # computed tone centroid
    tw = sum(m[2] for m in masses)
    cx0 = sum(m[0] * m[2] for m in masses) / tw
    cy0 = sum(m[1] * m[2] for m in masses) / tw
    p.circle([cx0, cy0], 16, fill="#ffffff")
    p.line([cx0 - 22, cy0], [cx0 + 22, cy0], **stroke(1.8, color=RUBRIC))
    p.line([cx0, cy0 - 22], [cx0, cy0 + 22], **stroke(1.8, color=RUBRIC))
    p.circle([cx0, cy0], 10, fill="none", **stroke(1.8, color=RUBRIC))
    p.line([fx + pw2 / 2, fy + 6], [fx + pw2 / 2, fy + ph2 - 6],
           **stroke(0.8, color=SUB))
    # annotations at right — fixed slots so none collide
    ax = fx + pw2 + 26
    notes = [
        (fy + 16, "TONES — six heavy grounds, one grey constant, a graded strip"),
        (fy + 130, "MEASURES — three columns on one module; margins as the frame"),
        (fy + 244, "SHAPES — one rectangle, repeated; harmony by one shape"),
        (max(cy0 - 8, fy + 358),
         "the computed centre of tone-weight, landing on the column axis"),
    ]
    for ny, nt in notes:
        p.line([ax - 12, ny + 8], [ax - 2, ny + 8], **stroke(1, color=RUBRIC))
        p.text([ax, ny, W - M - ax - 10, 56], nt, style="cap")
    caption(p, [M, 756, CW, 54],
            "This book, weighed: plate 25's page rebuilt in miniature from its own "
            "geometry, its tone-weighted centre computed as §3.4 computes it. The "
            "cross lands a hair below the optical centre, on the middle column's "
            "axis — the page rests because its sums do.")
    p.text([M, 836, CW, 80],
           "Any page will submit to this audit. Redraw it as tones, measures and "
           "shapes, find the unanswered attraction, and you have found the thing "
           "that has been bothering you about it.",
           style="body")


def pg_pure_page_text(b):
    p = new_page(b, "pure-page", chapter=CH3)
    y = section_head(p, 96, "3.8 · THE PAGE AS PURE DESIGN", "One system, three crafts",
                     lead="His own bridge: proportion means the same “whether in a "
                          "house, on the page of a book, or in the formation of a "
                          "single letter.”")
    p.text([M, y, CW, 108],
           "So the three chapters of this book so far are one subject. The mapping "
           "below is the author's, but every row of it is licensed by that "
           "sentence — the Ross terms, translated into the working matter of "
           "typography and colour.",
           style="body")
    framed_panel(p, [M, y + 128, CW, 330])
    rows = [
        ("TONES", "weight & colour", "type weight, ink greys, accent duty",
         "ch. I §1.9 · ch. II §2.8"),
        ("MEASURES", "size & space", "modular scale, leading, margins, grid",
         "ch. I §1.10–1.13"),
        ("SHAPES", "form", "letterforms, text blocks, plates",
         "ch. I §1.2–1.5"),
        ("RHYTHM", "regular change", "baselines, word-spaces, scale ratio",
         "§3.6"),
        ("BALANCE", "opposed attractions", "the spread at rest; the steelyard",
         "§3.3–3.5"),
        ("HARMONY", "likeness in accord", "one family, one palette, one module",
         "ch. I §1.6 · ch. II §2.11"),
    ]
    yy = y + 152
    for term, gloss, means, where in rows:
        p.text([M + 24, yy, 110, 13], term, style="labelR")
        p.text([M + 138, yy, 130, 26], gloss, style="cap")
        p.text([M + 272, yy - 2, 260, 40], means, style="bodyS")
        p.text([M + CW - 130, yy, 118, 26], where,
               style={"class": "cap", "font_size": 9})
        yy += 52
    caption(p, [M, y + 478, CW, 40],
            "The Ross system, mapped onto the book's own crafts — an author's "
            "table. Nothing in chapters I and II escapes the six rows.")
    p.text([M, y + 542, CW, 108],
           "This is why the old books keep working. Tools change every decade; "
           "attractions do not. A theory written for spot and ground survives "
           "translation to pixel and viewport because the eye it describes has not "
           "shipped a new version.",
           style="body")


def pg_grid_plate(b):
    p = new_page(b, "grid-plate", chapter=CH3)
    y = section_head(p, 96, "3.9 · THE GRID", "A balance instrument",
                     lead="The modern grid is the steelyard institutionalised: a "
                          "frame that quantises attractions before they argue.")
    framed_panel(p, [M, y + 6, CW, 520])
    # a mini page with a 6-column grid and balanced blocks
    gx, gy = M + 90, y + 40
    gw, gh = CW - 300, 440
    p.rect([gx, gy, gw, gh], fill="#ffffff")
    p.rect([gx, gy, gw, gh], fill="none", **stroke(1.2, color=SUB))
    cols, gut = 6, 8
    cw3 = (gw - 32 - (cols - 1) * gut) / cols
    for i in range(cols):
        p.rect([gx + 16 + i * (cw3 + gut), gy + 16, cw3, gh - 32],
               fill=mix(RUBRIC, "#ffffff", 0.93))
    # blocks: heavy image 4 cols, text 2 cols; caption counterweight
    p.rect([gx + 16, gy + 40, 4 * cw3 + 3 * gut, 190], fill=INK)
    for i in range(9):
        p.rect([gx + 16 + 4 * (cw3 + gut), gy + 44 + i * 20,
                2 * cw3 + gut - (18 if i % 4 == 3 else 0), 8],
               fill=mix(INK, "#ffffff", 0.45))
    p.rect([gx + 16 + 2 * (cw3 + gut), gy + 260, 2 * cw3 + gut, 120],
           fill=mix(INK, "#ffffff", 0.72))
    for i in range(4):
        p.rect([gx + 16, gy + 262 + i * 22, cw3 * 2 - 12, 8],
               fill=mix(INK, "#ffffff", 0.45))
    p.rect([gx + 16 + 4 * (cw3 + gut), gy + 340, 1.4 * cw3, 40], fill=INK)
    ax = gx + gw + 26
    for ny, nt in [(gy + 60, "the heavy measure sits high\nand left of the axis"),
                   (gy + 270, "middle-tone mass answers it\nacross the centre"),
                   (gy + 344, "a small black, far out on the\nbeam, completes the sum"),
                   (gy + 410, "columns quantise every arm\nto a shared module")]:
        p.line([ax - 12, ny + 8], [ax - 2, ny + 8], **stroke(1, color=RUBRIC))
        p.text([ax, ny, W - M - ax - 4, 44], nt, style="cap")
    caption(p, [M, y + 548, CW, 54],
            "An asymmetric spread audited by the beam — an author's construction. "
            "The grid does not create the balance; it makes the arithmetic "
            "repeatable, page after page, by many hands.")
    p.text([M, y + 626, CW, 66],
           "Batchelder's quarter-inch squared paper, Ross's common unit, the "
           "printer's em, the interface designer's 8-point step: one instrument, "
           "four names, a century of service.",
           style="body")


def pg_ch3_close(b):
    p = new_page(b, "ch3-close", chrome=False, folio=True)
    p.text([M, 170, CW, 16], "CHAPTER III · A SUMMARY IN FIVE LINES", style="kickerC")
    lines = [
        "A composition contains only tones, measures and shapes.",
        "Balance is repose: every attraction answered, the sums agreeing.",
        "The steelyard makes asymmetry as lawful as symmetry.",
        "Rhythm is repetition with governed change — the page's own drum.",
        "Harmony is manufactured by the common unit, not hoped for.",
    ]
    y = 280
    for i, ln in enumerate(lines):
        p.text([M + 40, y, 40, 40], ["I", "II", "III", "IV", "V"][i], style="h3r")
        p.text([M + 110, y - 4, CW - 150, 60], ln, style="lead")
        y += 92
    p.rect([W / 2 - 30, y + 30, 60, 2], fill="rubric")
    # beam motif
    by = y + 84
    p.line([W / 2 - 120, by], [W / 2 + 120, by], **stroke(2, color=SUB))
    p.polygon([[W / 2 - 6, by + 18], [W / 2 + 6, by + 18], [W / 2, by + 2]],
              fill=RUBRIC)
    p.circle([W / 2 - 42, by - 16], 15, fill=SUB)
    p.circle([W / 2 + 117, by - 10], 9, fill=SUB)
    p.text([0, y + 150, W, 14], "COLOUR AT WORK FOLLOWS — THE LAW LEAVES THE LABORATORY",
           style="attrC")

# --------------------------------------------------------------------------- #
# CHAPTER IV — COLOUR AT WORK (pp. 75-90)                                      #
# After Chevreul's applications (Principles of Harmony and Contrast, Part II   #
# and Third Division). Ordinals cite the full-translation corpus edition.      #
# --------------------------------------------------------------------------- #

# Chevreul's §516 ranking of black type on coloured papers (1839), with this
# book's hex approximations of the papers. The ratios are computed at build
# time and drawn beside his order — the re-measurement of §4.6.
CHEVREUL_PAPERS = [
    ("1°", "White", "#ffffff"),
    ("2°", "light Yellow", "#f4ead0"),
    ("3°", "light Yellow-Green", "#e6ecc6"),
    ("4°", "light Orange", "#f5ddba"),
    ("5°", "light Blue", "#ccdcec"),
    ("6°", "Crimson-Red", "#c05068"),
    ("7°", "deep Orange", "#d07a2a"),
    ("8°", "deep Red", "#9c2f34"),
    ("9°", "deep Violet", "#584371"),
]
TYPE_INK = "#17130e"


def pg_ch4_opener(b):
    p = new_page(b, "ch4-opener", chrome=False, folio=False, bg="night")
    p.text([M, 200, CW, 24], "CHAPTER IV", style="kickerC")
    p.text([0, 240, W, 170], "IV", style={"class": "cnum", "align": "center"})
    p.text([0, 430, W, 80], "Colour at Work",
           style={"class": "hcover", "font_size": 76, "color": "#f4ead6"})
    p.rect([W / 2 - 30, 560, 60, 2], fill="rubric")
    p.text([120, 610, W - 240, 90],
           "On the law applied — in the tapestry shed, the room, the garden, "
           "the wardrobe and the printing house — and on the oldest reading "
           "experiment, re-measured.",
           style={"class": "leadC", "color": "#d8c9ae"})
    # opener motif: paper chips ranked
    bw = (W - 2 * 190) / 9
    for i, (_, _, hx) in enumerate(CHEVREUL_PAPERS):
        p.rect([190 + i * bw, 796, bw - 5, 40], fill=hx)


def pg_gobelins_text(b):
    p = new_page(b, "gobelins-1", chapter=CH4)
    y = section_head(p, 96, "4.1 · THE GOBELINS COMPLAINT", "Where application began",
                     lead="The most consequential quality complaint in the history "
                          "of design was filed against a dye vat that was innocent.")
    p.text([M, y, CW, 170],
           "Chapter II told the story's first half: weavers at the Gobelins "
           "manufactory reported that the black yarns were feeble beside the blues, "
           "and Chevreul the chemist found the dyes beyond reproach. The defect was "
           "optical — every colour tints its neighbour with its own complement — "
           "and the law of simultaneous contrast followed. This chapter is the "
           "second half: what Chevreul did next. Rather than file the law away as "
           "science, he spent five hundred pages walking it through every trade "
           "that puts one colour beside another.",
           style="body")
    p.text([M, y + 192, CW, 130],
           "His own inventory of the territory: “I have thus been led to extend "
           "it to the arts of tapestry” — and onward through carpets, dress, "
           "gardens, apartments, painting and printing. The pattern is always the "
           "same, and it is the pattern of this chapter: the material is blameless; "
           "the neighbourhood is responsible; therefore design the neighbourhood.",
           style="body")
    p.text([M, y + 344, CW, 90],
           "Read that way, Chevreul is not a colour theorist at all. He is the "
           "first design reviewer — the first professional to be handed a defect "
           "report and to locate the bug in perception rather than production.",
           style="body")


def pg_gobelins_plate(b):
    p = new_page(b, "gobelins-2", chapter=CH4)
    framed_panel(p, [M, 90, CW, 620])
    # the weaver's problem: identical black hanks on two grounds
    for k, (ground, gname, verdict) in enumerate([
            (hue("Blue"), "WOVEN BESIDE BLUE", "the black warms — the eye adds orange"),
            ("#efe6d2", "WOVEN BESIDE UNDYED LINEN", "the same black reads full and cold")]):
        bx = M + 30 + k * (CW - 60) / 2
        bw2 = (CW - 60) / 2 - 20
        p.rect([bx, 130, bw2, 330], fill=ground)
        # yarn hank: parallel strokes
        for i in range(9):
            xx = bx + bw2 * 0.28 + i * bw2 * 0.055
            p.polyline([(xx, 180), (xx - 10, 250), (xx + 6, 330), (xx - 4, 410)],
                       smooth=True, fill="none",
                       **stroke(7, color=mix("#17130e", ground, 0.06 + 0.012 * i),
                                cap="round"))
        p.text([bx, 470, bw2, 12], gname, style="labelC")
        p.text([bx, 490, bw2, 26], verdict, style="capC")
    # the extension list
    p.text([M + 30, 546, CW - 60, 13],
           "THE LAW'S ITINERARY — CHEVREUL'S OWN LIST", style="labelR")
    arts = ["tapestry", "carpets", "dress", "gardens", "apartments",
            "picture-framing", "calico", "printing with types"]
    xx = M + 30
    yy = 572
    for a in arts:
        wpx = 9.2 * len(a) + 28
        if xx + wpx > M + CW - 30:
            xx = M + 30
            yy += 40
        p.rect([xx, yy, wpx, 28], fill="none", **stroke(1, color=SUB))
        p.text([xx, yy + 7, wpx, 14], a.upper(),
               style={"class": "labelC", "font_size": 9})
        xx += wpx + 12
    caption(p, [M, 736, CW, 54],
            "The weaver's problem, drawn: one black yarn, two neighbourhoods — an "
            "author's reconstruction of the Gobelins complaint. Below, the trades "
            "through which Chevreul then marched the law.")
    p.text([M, 812, CW, 80],
           "Every section that follows is one stop on that itinerary, and each "
           "ends the same way: with the law doing quietly what taste was thought "
           "to do mysteriously.",
           style="body")


def pg_room_text(b):
    p = new_page(b, "room-1", chapter=CH4)
    y = section_head(p, 96, "4.2 · THE ROOM", "Assortment as architecture",
                     lead="A furnished room is a composition the occupant lives "
                          "inside — walls, hangings, borders and furniture, all "
                          "obeying the law together.")
    p.text([M, y, CW, 150],
           "Chevreul treats the interior exactly as he treats the page: as figure "
           "and ground at scale. The dominant surface — the hangings — sets the "
           "scale; everything laid against it either agrees by analogy or answers "
           "by contrast. His rule for the border is the palette recipe of §2.11, "
           "sixty years early: “harmony of contrast is the most suitable to "
           "papers of a uniform pure colour … consequently we recommend for the "
           "dominant colour of the border the complementary of that of the "
           "hangings.”",
           style="body")
    p.text([M, y + 190, CW, 130],
           "And his warning against busy pattern is a whole modern debate settled "
           "in a clause: “patterns for hangings must recommend themselves more "
           "by opposite colours than by minute finish in the details.” The eye "
           "reads a wall at wall distance. Detail invisible at that distance is "
           "not richness; it is mud.",
           style="body")
    p.text([M, y + 340, CW, 84],
           "The plate opposite draws a wall elevation twice — once assorted by the "
           "law, once by accumulation — with the same inventory of parts.",
           style="body")


def pg_room_plate(b):
    p = new_page(b, "room-2", chapter=CH4)
    framed_panel(p, [M, 90, CW, 620])
    for k in range(2):
        bx = M + 30 + k * (CW - 60) / 2
        bw2 = (CW - 60) / 2 - 20
        if k == 0:
            wall = tone(hue("Blue-green"), -0.55)
            border = tone(hue("Red"), -0.12)
            p.rect([bx, 130, bw2, 380], fill=wall)
            p.rect([bx, 130, bw2, 24], fill=border)
            p.rect([bx, 396, bw2, 114], fill=tone(hue("Blue-green"), 0.30))
            p.rect([bx, 392, bw2, 6], fill=border)
            # one picture, framed in the wall's own deep tone
            p.rect([bx + bw2 * 0.30, 210, bw2 * 0.40, 120],
                   fill=tone(hue("Blue-green"), 0.5))
            p.rect([bx + bw2 * 0.33, 222, bw2 * 0.34, 96], fill="#efe6d2")
            # a settee as a middle tone
            p.rect([bx + bw2 * 0.18, 430, bw2 * 0.64, 56],
                   fill=tone(hue("Red"), 0.35))
            p.text([bx, 546, bw2, 12], "ASSORTED — BORDER COMPLEMENTARY,",
                   style="labelC")
            p.text([bx, 562, bw2, 12], "WAINSCOT FROM THE WALL'S OWN SCALE",
                   style="labelC")
        else:
            p.rect([bx, 130, bw2, 380], fill="#c9b791")
            clutter = [hue("Violet"), hue("Yellow-orange"), hue("Green"),
                       hue("Red-violet"), hue("Blue"), hue("Orange")]
            for i, c in enumerate(clutter):
                p.rect([bx + 14 + (i % 3) * bw2 * 0.31, 168 + (i // 3) * 130,
                        bw2 * 0.26, 100], fill=c)
            p.rect([bx, 396, bw2, 114], fill="#7a6a4d")
            p.rect([bx + bw2 * 0.18, 430, bw2 * 0.64, 56], fill=hue("Red-orange"))
            p.text([bx, 546, bw2, 12], "ACCUMULATED — SIX DOMINANTS,",
                   style="labelC")
            p.text([bx, 562, bw2, 12], "EVERY NEIGHBOUR AT WAR", style="labelC")
    caption(p, [M, 736, CW, 54],
            "One wall dressed twice — an author's construction on Chevreul's "
            "interior chapters. Left, the border takes the hangings' complement "
            "and the wainscot deepens the wall's own scale; right, no border, no "
            "plan — six unrelated dominants, and the room argues with itself.")
    p.text([M, 812, CW, 84],
           "The room is the palette recipe at architectural scale: one ground, "
           "one answering accent, the rest drawn from the ground's own scale. "
           "Swap ‘wall’ for ‘screen’ and the clause needs no other edit.",
           style="body")


def pg_garden_text(b):
    p = new_page(b, "garden-1", chapter=CH4)
    y = section_head(p, 96, "4.3 · THE GARDEN", "Planting by the law",
                     lead="Horticulture had colour advice for centuries — “in "
                          "a vague and general manner.” Chevreul replaces it "
                          "with prescriptions.")
    p.text([M, y, CW, 150],
           "Once the law is stated, “the grouping of flowers will present no "
           "difficulty, since it will only be a simple conclusion from facts.” "
           "The prescriptions follow like theorems. Complementary masses first: "
           "“blue flowers and orange flowers, yellow flowers and violet "
           "flowers.” Red needs no partner planted for it, since “rose or "
           "red flowers contrast with their own leaves” — the rose bed carries "
           "its complement on its stems.",
           style="body")
    p.text([M, y + 190, CW, 150],
           "Then the separations, which are the same lesson as plate 33's warning "
           "about neighbouring scales: “we must separate pink flowers from "
           "those that are either scarlet or crimson; orange flowers from "
           "orange-yellow flowers; blue flowers from violet-blue flowers.” "
           "Near-likeness reads as error in a border exactly as it does in a "
           "palette. And to the objection that green foliage drowns the whole "
           "exercise, his answer is the plate opposite: the leaves are not the "
           "enemy of the contrast but its ground.",
           style="body")


def pg_garden_plate(b):
    p = new_page(b, "garden-2", chapter=CH4)
    framed_panel(p, [M, 90, CW, 620])
    leaf = tone(hue("Green"), 0.15)
    p.rect([M + 30, 130, CW - 60, 420], fill=mix(leaf, "#ffffff", 0.18))

    def bed(cx, cy, r, colours, n=14):
        for i in range(n):
            a = i * 2.399963  # golden angle: even, natural scatter
            rr = r * math.sqrt((i + 0.5) / n)
            p.circle([cx + rr * math.cos(a), cy + rr * math.sin(a)],
                     7.5, fill=colours[i % len(colours)])
    # complementary beds
    bed(M + 140, 250, 62, [hue("Blue")])
    bed(M + 272, 250, 62, [hue("Orange")])
    p.text([M + 70, 336, 280, 12], "BLUE WITH ORANGE — THE FULL CHORD",
           style="labelC")
    bed(M + 430, 250, 62, [hue("Yellow")])
    bed(M + 548, 250, 56, [hue("Violet")])
    p.text([M + 396, 336, 224, 12], "YELLOW WITH VIOLET", style="labelC")
    # roses against their own leaves
    bed(M + 150, 448, 66, [tone(hue("Red"), -0.15)])
    p.text([M + 60, 534, 280, 12], "RED AGAINST ITS OWN LEAVES", style="labelC")
    # the forbidden neighbours
    bed(M + 440, 448, 54, [mix(hue("Red-violet"), "#ffffff", 0.35)])
    bed(M + 552, 448, 54, [hue("Red")])
    p.text([M + 372, 534, 264, 12], "PINK AGAINST SCARLET — SEPARATE THESE",
           style={"class": "labelC", "font_size": 9})
    p.rect([M + 497, 402, 3, 92], fill=RUBRIC)
    caption(p, [M, 736, CW, 54],
            "Chevreul's borders, drawn as beds on their ground of leaves — an "
            "author's construction from his prescriptions. The green is not "
            "noise: it is the field that lets red bloom unpartnered, and the "
            "red bar marks the association he orders broken up.")
    p.text([M, 812, CW, 84],
           "A garden is a palette that grows. The gardener's advantage over the "
           "designer is that the ground colour is fixed by botany — which is "
           "why the law's garden chapter reads like a solved exercise.",
           style="body")


def pg_person_text(b):
    p = new_page(b, "person-1", chapter=CH4)
    y = section_head(p, 96, "4.4 · THE PERSON", "The garment tints the face",
                     lead="The most intimate application: whatever colour is worn "
                          "near the face lends the face its complement.")
    p.text([M, y, CW, 168],
           "The mechanism is the law of §2.3 with a human ground. “A delicate "
           "green is … favourable to all fair complexions which are deficient "
           "in rose” — because green's complement is rose, and the eye "
           "delivers it to the skin. “Yellow imparts violet to a fair skin,” "
           "and is therefore less kind; violet returns the favour, imparting "
           "“greenish-yellow”; while “blue imparts orange, which is "
           "susceptible of allying itself favourably … to the light flesh "
           "tints.” Every verdict is the same theorem with a different ground.",
           style="body")
    p.text([M, y + 208, CW, 130],
           "Chevreul's millinery pages run the theorem through bonnets and "
           "trimmings with the patience of a test suite — and when a colour "
           "fails against a complexion, his remedy is an interposed neutral: "
           "hair, ribbon, or “wreaths or detached flowers” to break the "
           "juxtaposition. The modern name for that remedy is a border, a "
           "keyline, a breathing space.",
           style="body")
    p.text([M, y + 358, CW, 60],
           "The plate opposite runs the four draperies against two grounds — "
           "abstracted to swatches, as befits a book and the dignity of readers.",
           style="body")


def pg_person_plate(b):
    p = new_page(b, "person-2", chapter=CH4)
    framed_panel(p, [M, 90, CW, 620])
    skins = [("A FAIR GROUND", "#ecd3c0"), ("A DEEP GROUND", "#8a5a3c")]
    drapes = [("GREEN", tone(hue("Green"), -0.2), "imparts rose"),
              ("YELLOW", hue("Yellow"), "imparts violet"),
              ("VIOLET", hue("Violet"), "imparts green-yellow"),
              ("BLUE", hue("Blue"), "imparts orange")]
    for k, (sname, skin) in enumerate(skins):
        bx = M + 30 + k * (CW - 60) / 2
        bw2 = (CW - 60) / 2 - 20
        p.text([bx, 128, bw2, 13], sname, style="labelRC")
        for i, (dn, dc, effect) in enumerate(drapes):
            yy = 160 + i * 100
            p.rect([bx + 20, yy, bw2 - 40, 78], fill=dc)
            p.circle([bx + bw2 / 2, yy + 39, ], 26, fill=skin)
            p.text([bx + 20, yy + 82, bw2 - 40, 12],
                   f"{dn} — {effect}",
                   style={"class": "capC", "font_size": 9})
    caption(p, [M, 736, CW, 54],
            "Four draperies, two grounds, abstracted to chip and disc — an "
            "author's construction on Chevreul's complexion chapters. Stare at "
            "each disc and its ground delivers the promised complement; the "
            "effect is strongest at the boundary, which is why his remedy for a "
            "failed pairing is always an interposed neutral.")
    p.text([M, 812, CW, 84],
           "Substitute ‘avatar’ for ‘complexion’ and ‘theme’ for ‘bonnet’ and "
           "the chapter reads as written this year: every profile picture sits "
           "on a ground that is quietly recolouring it.",
           style="body")


def pg_print_text(b):
    p = new_page(b, "print-text", chapter=CH4)
    y = section_head(p, 96, "4.5 · THE PRINTED PAGE", "Types upon coloured paper",
                     lead="At the itinerary's end Chevreul arrives on our ground: "
                          "black type, coloured paper, and the question of what "
                          "reads best.")
    p.text([M, y, CW, 150],
           "The Third Division of his applications takes printing in order — "
           "calico, paper-hangings, and at last “printing with types upon "
           "coloured paper.” The calico chapter issues the warning every "
           "brand-guideline PDF still repeats: “the pattern may appear to be "
           "very different in colour from what was intended, and from what it "
           "really is.” The type chapter then runs the experiment this book "
           "has been circling since plate 37.",
           style="body")
    p.text([M, y + 190, CW, 150],
           "His protocol is startlingly modern. He separates the cases — reading "
           "“of some minutes'” duration from reading “of some hours'”; "
           "daylight from artificial light — notes that letters present “much "
           "less extent of surface than the paper which serves as their ground,” "
           "and even accounts for afterimage fatigue, “the impression of their "
           "respective complementaries,” during long reading. Then he ranks "
           "nine papers by eye, “commencing with those which appeared to me "
           "the easiest to read.”",
           style="body")
    p.text([M, y + 380, CW, 70],
           "The plate opposite prints his ranking — and re-measures it with the "
           "instrument his century did not have.",
           style="body")


def pg_remeasure_plate(b):
    p = new_page(b, "remeasure", chapter=CH4)
    framed_panel(p, [M, 90, CW, 700])
    p.text([M + 30, 112, CW - 60, 13],
           "CHEVREUL'S §516 RANKING (1839) — RE-MEASURED (WCAG 2.x, 2026)",
           style="labelR")
    rows_y = 148
    rh = 64
    ratios = [wcag_ratio(TYPE_INK, hx) for _, _, hx in CHEVREUL_PAPERS]
    max_ratio = max(ratios)
    thr_x = M + 460 + (CW - 520) * 4.5 / max_ratio
    p.line([thr_x, rows_y - 6], [thr_x, rows_y + 9 * rh - 8],
           **stroke(1.2, color=GOLD))
    p.text([thr_x - 34, rows_y + 9 * rh, 110, 12], "WCAG 4.5:1", style="cap")
    modern_rank = {i: r + 1 for r, i in
                   enumerate(sorted(range(9), key=lambda i: -ratios[i]))}
    for i, ((rank, name, hx), ratio) in enumerate(zip(CHEVREUL_PAPERS, ratios)):
        yy = rows_y + i * rh
        p.text([M + 30, yy + 14, 30, 14], rank, style="label")
        p.rect([M + 70, yy, 220, rh - 14], fill=hx)
        p.rect([M + 70, yy, 220, rh - 14], fill="none", **stroke(1, color=HAIR))
        p.text([M + 82, yy + 12, 200, 26], "legibility",
               style={"font_family": SERIF, "font_size": 17, "color": TYPE_INK})
        p.text([M + 302, yy + 4, 150, 13], name.upper(),
               style={"class": "label", "font_size": 8.5})
        p.text([M + 302, yy + 22, 150, 12],
                f"modern rank {modern_rank[i]}°",
                style={"class": "cap", "font_size": 9,
                       "color": (RUBRIC if modern_rank[i] != i + 1 else SUB)})
        bar_w = (CW - 520) * ratio / max_ratio
        p.rect([M + 460, yy + 8, bar_w, 22],
               fill=(INK if modern_rank[i] == i + 1 else RUBRIC))
        p.text([M + 466 + bar_w, yy + 12, 70, 13], f"{ratio:.1f}:1", style="label")

    caption(p, [M, 816, CW, 54],
            "The 1839 ranking, re-measured. Chevreul's order by eye against the "
            "contrast ratio of near-black ink on each paper (papers are this "
            "book's hex approximations). Black bars: his rank and the formula "
            "agree; red bars mark the disagreements.")
    p.text([M, 892, CW, 84],
           "Seven of nine placings survive a formula written a century and a half "
           "later. The two that move are the two he flagged himself — the deep "
           "warm papers, where hue pleads and luminance disagrees.",
           style="body")


def pg_remeasure_analysis(b):
    p = new_page(b, "remeasure-2", chapter=CH4)
    y = section_head(p, 96, "4.6 · 1839, RE-MEASURED", "What the eye knew early",
                     lead="An experiment that survives its own re-measurement is "
                          "rarer than a law. Chevreul's mostly does.")
    p.text([M, y, CW, 216],
           "The agreement is the headline: ranked by one careful pair of eyes in "
           "1839, the light papers fall in almost exactly the order that relative "
           "luminance assigns them today. The formula is measuring what his eye "
           "was reporting — tone contrast between figure and ground — which is the "
           "thesis of §2.9 arriving with a hundred and eighty years of "
           "corroboration. Where eye and formula part ways, on the crimson and the "
           "deep orange, the disagreement is itself informative: saturated warm "
           "grounds flatter type with hue-difference while quietly starving it of "
           "tone-difference. The formula refuses the flattery.",
           style="body")
    quote_block(p, y + 238,
                "I have every reason to believe that other eyes than mine would "
                "require some change in the order I have assigned to the preceding "
                "assortments.", None)
    p.text([M, y + 342, CW, 14], "CHEVREUL, §517 — THE FIRST REVIEWER'S CAVEAT",
           style="attr")
    p.text([M, y + 386, CW, 130],
           "That sentence deserves framing in every design studio. The author of "
           "the strongest colour law of his century publishes a ranking and "
           "immediately declares it one observer's data, awaiting other eyes. The "
           "modern formula is those other eyes — millions of them, averaged into a "
           "luminance curve — and it largely voted with him.",
           style="body")
    p.text([M, y + 536, CW, 130],
           "One more anticipation deserves notice. Ranking by duration, he warns "
           "that the strongest pairing “will be more favourable in a reading of "
           "short duration, while it will be less so in a prolonged reading, "
           "because … it will fatigue the eye more” (§503). Maximum contrast "
           "is a sprinter: the whole modern argument over paper-white screens and "
           "reading modes, run once, by lamplight, in 1839.",
           style="body")


def pg_dyer_text(b):
    p = new_page(b, "dyer", chapter=CH4)
    y = section_head(p, 96, "4.7 · THE METHOD", "The designer as dyer",
                     lead="Strip the nineteenth century from Chevreul's procedure "
                          "and what remains is the working loop of every design "
                          "practice worth the name.")
    framed_panel(p, [M, y + 6, CW, 380])
    steps = [
        ("THE COMPLAINT", "someone trusts their eyes and says so",
         "the weavers report feeble blacks"),
        ("THE AUDIT", "test the material before the theory",
         "the dye is assayed — and acquitted"),
        ("THE LAW", "name the mechanism, not the symptom",
         "colours tint neighbours with complements"),
        ("THE APPLICATION", "walk the law through every trade it touches",
         "tapestry, rooms, gardens, dress, the page"),
        ("THE CAVEAT", "publish the data and its observer",
         "“other eyes than mine…” (§517)"),
    ]
    yy = y + 34
    for i, (nm, gloss, ex) in enumerate(steps):
        p.circle([M + 46, yy + 16], 15, fill="none", **stroke(1.6, color=RUBRIC))
        p.text([M + 38, yy + 8, 20, 14], str(i + 1),
               style={"class": "labelR", "align": "center"})
        p.text([M + 78, yy, 190, 14], nm, style="labelR")
        p.text([M + 78, yy + 18, 240, 30], gloss, style="cap")
        p.text([M + 340, yy + 2, CW - 380, 46], ex, style="bodyS")
        if i < 4:
            p.line([M + 46, yy + 33], [M + 46, yy + 56], **stroke(1, color=HAIR))
        yy += 70
    caption(p, [M, y + 406, CW, 40],
            "Chevreul's method, abstracted — an author's synthesis. Complaint, "
            "audit, law, application, caveat: the quality loop, first run in 1839.")
    p.text([M, y + 470, CW, 128],
           "This book has tried to run the same loop on itself: its quotations "
           "audited against their sources, its contrast claims measured, its own "
           "pages weighed by the principles they teach, and its residual limits "
           "declared in the apparatus. Where it fails, the method — not the "
           "author's taste — is the court of appeal.",
           style="body")


def pg_ch4_quote_plate(b):
    p = new_page(b, "ch4-quote", chapter=CH4, chrome=False, folio=True, bg="night")
    p.text([M, 150, CW, 16], "THE LAW, LEAVING THE LABORATORY", style="kickerC")
    display_quote(p, [110, 280, W - 220, 300],
                  "In viewing the law of simultaneous contrast of colours with "
                  "reference to its application, and in submitting to experiment "
                  "all the consequences which appeared to me to result from it, I "
                  "have thus been led to extend it to the arts of tapestry.",
                  size=24, line_height=1.5, color=CREAM, align="center")
    p.text([0, 620, W, 14], "CHEVREUL, PRINCIPLES OF HARMONY AND CONTRAST — INTRODUCTION",
           style="attrC")
    p.rect([W / 2 - 30, 668, 60, 2], fill="rubric")
    p.text([140, 706, W - 280, 140],
           "…and to everything after tapestry. A law that stops at the laboratory "
           "door is a curiosity. Chevreul's walked out, took work in a dozen "
           "trades, and has not been unemployed since.",
           style={"font_family": SERIF, "font_size": 15, "color": "#d8c9ae",
                  "line_height": 1.65, "align": "center"})


def pg_ch4_close(b):
    p = new_page(b, "ch4-close", chrome=False, folio=True)
    p.text([M, 170, CW, 16], "CHAPTER IV · A SUMMARY IN FIVE LINES", style="kickerC")
    lines = [
        "The material is blameless; the neighbourhood is responsible.",
        "Every room, bed and bookshelf is an assortment obeying the law.",
        "Grounds flatter with hue while starving type of tone — measure them.",
        "An honest ranking names its observer and awaits other eyes.",
        "Application is the proof of a principle — walk it through the trades.",
    ]
    y = 280
    for i, ln in enumerate(lines):
        p.text([M + 40, y, 40, 40], ["I", "II", "III", "IV", "V"][i], style="h3r")
        p.text([M + 110, y - 4, CW - 150, 60], ln, style="lead")
        y += 92
    p.rect([W / 2 - 30, y + 30, 60, 2], fill="rubric")
    bw = 180 / 9
    for i, (_, _, hx) in enumerate(CHEVREUL_PAPERS):
        p.rect([W / 2 - 90 + i * bw, y + 70, bw - 2, 22], fill=hx)
    p.text([0, y + 130, W, 14], "THE APPARATUS FOLLOWS — SOURCES, PLATES, SPECIMENS",
           style="attrC")


CH3_PAGES[:] = [
    pg_ch3_opener, pg_third_craft, pg_terms_principles, pg_tone_balance_text,
    pg_tone_balance_plate, pg_steelyard_text, pg_steelyard_plate,
    pg_shape_balance_text, pg_shape_balance_plate, pg_rhythm_text,
    pg_rhythm_plate, pg_harmony_text, pg_book_weighed_plate, pg_pure_page_text,
    pg_grid_plate, pg_ch3_close,
]
CH4_PAGES[:] = [
    pg_ch4_opener, pg_gobelins_text, pg_gobelins_plate, pg_room_text,
    pg_room_plate, pg_garden_text, pg_garden_plate, pg_person_text,
    pg_person_plate, pg_print_text, pg_remeasure_plate, pg_remeasure_analysis,
    pg_dyer_text, pg_ch4_quote_plate, pg_ch4_close,
]

# --------------------------------------------------------------------------- #
# Assembly                                                                     #
# --------------------------------------------------------------------------- #

FRONT = [pg_half_title, pg_frontispiece, pg_title, pg_copyright, pg_contents,
         pg_preface]
CH1_PAGES = [
    pg_ch1_opener, pg_lineage_text, pg_lineage_plate, pg_essential_text,
    pg_skeleton_plate, pg_tool_text, pg_tool_plate, pg_serif_text, pg_serif_plate,
    pg_anatomy_lines, pg_anatomy_parts, pg_qualities_text, pg_qualities_table,
    pg_readableness, pg_beauty, pg_character, pg_proportion_text,
    pg_proportion_plate, pg_classification_text, pg_spec_garalde, pg_spec_didone,
    pg_spec_grot, pg_spec_humanist, pg_spec_mono, pg_weight, pg_scale, pg_spacing,
    pg_measure, pg_margins, pg_ch1_close,
]
END_PAGES = [pg_sources, pg_plate_list, pg_specimen_sources, pg_back_cover]


def build() -> DocumentBuilder:
    b = DocumentBuilder(title="The Letter & the Hue", profile="report", lang="en")
    for k, v in COLORS.items():
        b.define_color(k, v)
    for k, v in STYLES.items():
        b.define_text_style(k, **v)
    for fn in FRONT + CH1_PAGES + CH2_PAGES + CH3_PAGES + CH4_PAGES + END_PAGES:
        fn(b)
    return b


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__) or ".",
                                                  "typeface-and-colour.fg.yaml"))
    args = ap.parse_args()
    b = build()
    doc = b.build()
    report = validate_static_rules(doc)
    for issue in report.issues:
        print(issue.severity, issue.rule_id, issue.path, issue.message, sep="  ")
    problems = [("QUOTE-GATE", q) for q in check_quotes()]
    problems += [("NOTE-GATE", n) for n in check_notes()]
    for tag, msg in problems:
        print(tag, msg)
    if problems:
        return 1
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(serialize(doc, format="yaml"))
    print(f"pages={len(doc.pages)} ok={report.ok} -> {args.out}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
