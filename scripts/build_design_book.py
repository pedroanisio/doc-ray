# -*- coding: utf-8 -*-
"""Diretrizes de Design 2027 — FrameGraph book builder (reproducible).

Renders the PT-BR manuscript (docs/diretrizes-de-design-2027.md) into the
designed, paginated book ``docs/diretrizes-de-design-2027.pdf`` — a real type
specimen, colour swatches with the grey test, a 12-column grid, a hierarchy
ladder, drawn UI components and a data-viz plate — entirely via the FrameGraph
SDK: closed Chevreul palette, Johnston modular scale, one editorial system
(Princípio / Por quê / Diretrizes / Faça-Evite / Nota), auto TOC.

Pipeline (self-contained; no MCP server needed):
    build()  -> validated FrameGraph flow document
    render_page_svgs(doc)  -> one SVG per page
    cairosvg + pypdf  -> one A5 PDF, scaled to trim

Run:  python scripts/build_design_book.py
Deps: framegraph SDK on PYTHONPATH (FRAMEGRAPH_SRC), cairosvg, pypdf.
"""
from __future__ import annotations

import io
import os
import re
import sys

_FG_SRC = os.environ.get(
    "FRAMEGRAPH_SRC", "/home/admin/github-mirror/_framework/frameforge/src")
if _FG_SRC and _FG_SRC not in sys.path:
    sys.path.insert(0, _FG_SRC)

from framegraph.sdk.book import ChapterBuilder  # noqa: E402
from framegraph.sdk.chevreul import closed_palette, contrast_ratio  # noqa: E402
from framegraph.sdk.conform import render_page_svgs  # noqa: E402
from framegraph.sdk.flow import FlowBuilder  # noqa: E402
from framegraph.sdk.macros import md, span  # noqa: E402
from framegraph.sdk.metrics import wrap_text  # noqa: E402
from framegraph.sdk.model import HEAD_VERSION, validate_document  # noqa: E402

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANUSCRIPT = os.path.join(_HERE, "docs", "diretrizes-de-design-2027.md")
OUTPUT_PDF = os.path.join(_HERE, "docs", "diretrizes-de-design-2027.pdf")
A5_WIDTH_PT = 419.53  # A5 trim; page height follows the flow master's aspect

# ---------------------------------------------------------------------------- #
# 1 · The closed palette (the very canon the book teaches — self-demonstrating)
# ---------------------------------------------------------------------------- #
GROUND, INK, ACCENT = "#FBF9F4", "#22201C", "#A6432E"
_P = closed_palette(ground=GROUND, ink=INK, accent=ACCENT, quiet_steps=3).tokens()
Q_DARK = _P["quiet3"]   # darkest grey — secondary text
Q_MID = _P["quiet2"]
Q_LITE = _P["quiet1"]   # hairlines / rules
FACA = "#2C6E49"        # dark green — the "do" duty


def _rgb(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _hex(t):
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(v))) for v in t)


def tint(fg, bg, t):
    """t=0 -> fg, t=1 -> bg (mix toward the ground)."""
    a, b = _rgb(fg), _rgb(bg)
    return _hex(tuple(a[i] + (b[i] - a[i]) * t for i in range(3)))


def greyscale(c):
    r, g, b = _rgb(c)
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return _hex((y, y, y))


# tone-contrast gates (WCAG) — fail loudly if a text colour is illegible
for _name, _c in (("ink", INK), ("q_dark", Q_DARK), ("accent", ACCENT), ("faca", FACA)):
    _cr = contrast_ratio(_c, GROUND)
    assert _cr >= 4.5, f"{_name} {_c} contrast {_cr:.2f} < 4.5 on ground"

# ---------------------------------------------------------------------------- #
# 2 · Type system — Source Serif 4 (text) + Inter (display), one modular scale
# ---------------------------------------------------------------------------- #
SERIF = ["Source Serif 4", "Georgia", "serif"]
SANS = ["Inter", "Arimo", "sans-serif"]

BASE, RATIO = 10.5, 1.25
def _sz(i):  # noqa: E306
    return round(BASE * RATIO ** i, 2)

S_CAP = _sz(-1)     # 8.4  caption / kicker
S_BODY = _sz(0)     # 10.5 body
S_LEAD = _sz(1)     # 13.1 principle lead
S_H2 = _sz(2)       # 16.4 section head
S_H1 = _sz(4)       # 25.6 chapter head
S_PART = _sz(5)     # 32.0 part number
S_DISPLAY = 46.0    # cover display

CONTENT_W = 330.0   # A5 book-master text column (verified against render)

BODY = {"font_family": SERIF, "font_size": S_BODY, "color": INK,
        "line_height": 1.42, "align": "justify"}
BODY_L = {**BODY, "align": "left"}
LEAD = {"font_family": SERIF, "font_size": S_LEAD, "color": INK,
        "line_height": 1.36, "align": "left"}
NOTE = {"font_family": SERIF, "font_size": 10.0, "color": Q_DARK,
        "line_height": 1.4, "font_style": "italic", "align": "left"}
LIST = {"font_family": SERIF, "font_size": S_BODY, "color": INK, "line_height": 1.34}


def kicker_style(color=ACCENT):
    return {"font_family": SANS, "font_size": S_CAP, "font_weight": 700,
            "color": color, "letter_spacing": 1.7, "align": "left"}


# ---------------------------------------------------------------------------- #
# 3 · Manuscript parser -> flat typed blocks
# ---------------------------------------------------------------------------- #
_OL = re.compile(r"^\d+\.\s")
_UL = re.compile(r"^[-*]\s")


def parse(text):
    lines = text.split("\n")
    blocks, i, n = [], 0, len(lines)
    while i < n:
        raw = lines[i]
        s = raw.strip()
        if not s:
            i += 1
            continue
        if s == "---":
            blocks.append(("hr",))
            i += 1
            continue
        if s.startswith("### "):
            blocks.append(("h3", s[4:].strip())); i += 1; continue
        if s.startswith("## "):
            blocks.append(("h2", s[3:].strip())); i += 1; continue
        if s.startswith("# "):
            blocks.append(("h1", s[2:].strip())); i += 1; continue
        if _OL.match(raw):
            items = []
            while i < n and _OL.match(lines[i]):
                txt = _OL.sub("", lines[i].strip()); i += 1
                while (i < n and lines[i].strip() and not _OL.match(lines[i])
                       and not _UL.match(lines[i].lstrip())
                       and (lines[i][:1] in (" ", "\t"))):
                    txt += " " + lines[i].strip(); i += 1
                items.append(txt)
            blocks.append(("ol", items)); continue
        if _UL.match(raw):
            items = []
            while i < n and _UL.match(lines[i]):
                txt = _UL.sub("", lines[i].strip()); i += 1
                while (i < n and lines[i].strip() and not _UL.match(lines[i])
                       and not _OL.match(lines[i])
                       and (lines[i][:1] in (" ", "\t"))):
                    txt += " " + lines[i].strip(); i += 1
                items.append(txt)
            blocks.append(("ul", items)); continue
        buf = [s]; i += 1
        while (i < n and lines[i].strip() and not lines[i].strip().startswith("#")
               and not _OL.match(lines[i]) and not _UL.match(lines[i].lstrip())
               and lines[i].strip() != "---"):
            buf.append(lines[i].strip()); i += 1
        blocks.append(("p", " ".join(buf)))
    return blocks


# label detector on a paragraph: **Label.** rest  ->  (label, rest)
_LABEL = re.compile(r"^\*\*([^*]+?)\.?\*\*\s*(.*)$", re.S)
KICKERS = {
    "Princípio": "PRINCÍPIO", "Por quê": "POR QUÊ",
    "Diretrizes": "DIRETRIZES", "Faça / Evite": "FAÇA / EVITE",
    "Nota de aplicação": "NOTA DE APLICAÇÃO",
}


def strip_md(s):
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"\*(.+?)\*", r"\1", s)
    return s


# ---------------------------------------------------------------------------- #
# 4 · Chapter renderer
# ---------------------------------------------------------------------------- #
def render_chapter(cb, blocks, plate=None):
    pending = None   # 'Diretrizes' | 'Faça / Evite'
    for b in blocks:
        kind = b[0]
        if kind == "p":
            m = _LABEL.match(b[1])
            label = m.group(1).strip() if m else None
            if label in ("Diretrizes", "Faça / Evite"):
                if label == "Diretrizes":
                    if plate is not None:
                        plate(cb)      # illustrate before the guidelines
                    cb.spacer(6)
                    cb.para(KICKERS[label], style=kicker_style())
                pending = label        # 'Faça / Evite' -> next ul becomes the card
                continue
            if label in ("Princípio", "Por quê", "Nota de aplicação"):
                if label == "Nota de aplicação":
                    cb.spacer(7)
                    cb.add({"type": "figure", "break_inside": "avoid",
                            "object": _card(CONTENT_W, ACCENT, KICKERS[label],
                                            [(None, m.group(2))], tint_t=0.9,
                                            italic_body=True)})
                else:
                    cb.spacer(6)
                    cb.para(KICKERS[label], style=kicker_style())
                    cb.para(m.group(2), style=LEAD if label == "Princípio" else BODY)
                continue
            cb.para(b[1], style=BODY)   # continuation paragraph
        elif kind == "ol":
            cb.numbered(list(b[1]), style=LIST)
            pending = None
        elif kind == "ul":
            if pending == "Faça / Evite":
                do_avoid(cb, b[1])
            else:
                cb.bullet(list(b[1]), style=LIST)
            pending = None
        elif kind in ("h3",):
            cb.spacer(6)
            cb.para(b[1], style={"font_family": SANS, "font_size": S_H2,
                                 "font_weight": 700, "color": INK})


def _card(W, color, label, items, *, tint_t=0.84, italic_body=False):
    """A tinted card plate (drawn figure): top bar, kicker label, marked rows.

    ``items`` is a list of (marker|None, text). The flow renderer does not
    paint block backgrounds, so the card is a single group: bg rect first,
    then bar/label/wrapped-text drawn on top.
    """
    pad, mw, size, lh = 13.0, 16.0, 10.0, 13.6
    inner = W - 2 * pad
    fg = []
    y = pad
    bar_y = y
    y += 9
    label_y = y
    y += 15
    tstyle = {"font_family": SERIF, "font_size": size,
              "color": Q_DARK if italic_body else INK, "vertical_align": "top"}
    if italic_body:
        tstyle = {**tstyle, "font_style": "italic"}
    for mark, text in items:
        x0 = pad + (mw if mark else 0)
        tw = inner - (mw if mark else 0)
        lines = wrap_text(strip_md(text), width=tw, font_family=SERIF, font_size=size)
        if mark:
            fg.append({"type": "text", "box": [pad, y, mw, lh], "text": mark,
                       "style": {"font_family": SANS, "font_size": size,
                                 "font_weight": 700, "color": color,
                                 "vertical_align": "top"}})
        for k, ln in enumerate(lines):
            fg.append({"type": "text", "box": [x0, y + k * lh, tw, lh],
                       "text": ln, "style": tstyle})
        y += len(lines) * lh + (5 if mark else 3)
    H = y + pad - 2
    bg = [
        {"type": "rect", "box": [0, 0, W, H], "fill": tint(color, GROUND, tint_t),
         "radius": 5, "decorative": True},
        {"type": "rect", "box": [pad, bar_y, 40, 4], "fill": color, "decorative": True},
        {"type": "text", "box": [pad, label_y, inner, 13], "text": label,
         "style": {"font_family": SANS, "font_size": 8.4, "font_weight": 700,
                   "color": color, "letter_spacing": 1.6, "vertical_align": "top"}},
    ]
    return {"type": "group", "box": [0, 0, W, H], "children": bg + fg,
            "meta": {"no_overlap": False}}


def do_avoid(cb, items):
    faca, evite = [], []
    for it in items:
        low = strip_md(it).lower()
        if low.startswith("faça"):
            faca.append((("✓"), re.sub(r"(?i)^\*\*Faça\*\*\s*", "", it)))
        elif low.startswith("evite"):
            evite.append((("✕"), re.sub(r"(?i)^\*\*Evite\*\*\s*", "", it)))
        else:
            faca.append((("✓"), it))
    cb.spacer(5)
    cb.add({"type": "figure", "break_inside": "avoid",
            "object": _card(CONTENT_W, FACA, "FAÇA",
                            [(m, t) for m, t in faca], tint_t=0.85)})
    cb.spacer(7)
    cb.add({"type": "figure", "break_inside": "avoid",
            "object": _card(CONTENT_W, ACCENT, "EVITE",
                            [(m, t) for m, t in evite], tint_t=0.86)})


# ---------------------------------------------------------------------------- #
# 5 · Front / back matter
# ---------------------------------------------------------------------------- #
def cover(front, title, subtitle):
    W, H = CONTENT_W, 500.0
    ch = [
        {"type": "rect", "box": [0, 0, W, H], "fill": INK, "radius": 4},
        {"type": "rect", "box": [30, 74, 54, 6], "fill": ACCENT},
        {"type": "text", "box": [30, 96, W - 60, 60], "text": "DIRETRIZES",
         "style": {"font_family": SANS, "font_size": S_DISPLAY, "font_weight": 800,
                   "color": GROUND, "letter_spacing": 0.5, "vertical_align": "top"}},
        {"type": "text", "box": [30, 150, W - 60, 60], "text": "DE DESIGN",
         "style": {"font_family": SANS, "font_size": S_DISPLAY, "font_weight": 800,
                   "color": GROUND, "letter_spacing": 0.5, "vertical_align": "top"}},
        {"type": "text", "box": [30, 210, W - 60, 90], "text": "2027",
         "style": {"font_family": SANS, "font_size": 96, "font_weight": 800,
                   "color": ACCENT, "letter_spacing": 1.0, "vertical_align": "top"}},
        {"type": "text", "box": [30, 356, W - 60, 44],
         "text": subtitle,
         "style": {"font_family": SERIF, "font_size": 13.5, "font_style": "italic",
                   "color": tint(GROUND, INK, 0.18), "line_height": 1.4,
                   "vertical_align": "top"}},
        {"type": "rect", "box": [30, H - 60, W - 60, 1.2], "fill": tint(GROUND, INK, 0.5)},
        {"type": "text", "box": [30, H - 48, W - 60, 20],
         "text": "PRINCÍPIOS · PADRÕES · SISTEMAS",
         "style": {"font_family": SANS, "font_size": S_CAP, "font_weight": 700,
                   "color": tint(GROUND, INK, 0.3), "letter_spacing": 2.4,
                   "vertical_align": "top"}},
    ]
    front.add({"type": "figure", "object": {"type": "group", "box": [0, 0, W, H],
               "children": ch, "meta": {"no_overlap": False}},
               "break_inside": "avoid"})
    front.add({"type": "page_break"})


def front_section(front, title, blocks):
    front.heading(1, title, id="fs-" + re.sub(r"\W+", "-", title.lower())[:24])
    for b in blocks:
        if b[0] == "p":
            front.para(b[1], style=BODY)
        elif b[0] == "ul":
            front.bullet(list(b[1]), style=LIST)
        elif b[0] == "ol":
            front.numbered(list(b[1]), style=LIST)


def part_divider(front, title, intro_blocks):
    """A fresh-page divider: big accent part number/name (TOC anchor) + intro."""
    num, _, name = title.partition(" — ")
    front.spacer(140)   # push the divider down the page for air
    front.add({"type": "figure", "break_inside": "avoid", "object": {
        "type": "group", "box": [0, 0, CONTENT_W, 10],
        "children": [{"type": "rect", "box": [0, 3, 60, 6], "fill": ACCENT}]}})
    # heading carries the part into the TOC; restyle_headings sizes it
    front.heading(1, title, id="part-" + re.sub(r"\W+", "-", num.lower()))
    for b in intro_blocks:
        if b[0] == "p":
            front.para(b[1], style={**LEAD, "color": Q_DARK})


# ---------------------------------------------------------------------------- #
# 6 · Assembly
# ---------------------------------------------------------------------------- #
def restyle_headings(story):
    for fl in story:
        if fl.get("type") != "heading":
            continue
        st = fl.setdefault("style", {})
        if fl.get("level") == 1:
            t = fl.get("text", "")
            if t.startswith("Parte "):
                st.update({"font_family": SANS, "font_size": S_H1,
                           "font_weight": 800, "color": ACCENT,
                           "line_height": 1.08, "letter_spacing": 0.3})
            else:
                st.update({"font_family": SANS, "font_size": S_H1,
                           "font_weight": 800, "color": INK,
                           "line_height": 1.05})
        elif fl.get("level") == 2:
            st.update({"font_family": SANS, "font_size": S_H2,
                       "font_weight": 700, "color": INK})


def build():
    text = open(MANUSCRIPT, encoding="utf-8").read()
    blocks = parse(text)

    # split into front / parts+chapters / back
    title = "Diretrizes de Design 2027"
    subtitle = "Princípios, padrões e sistemas para produtos digitais"
    front = FlowBuilder()
    cover(front, title, subtitle)

    # colophon / half-title — sits in the upper-middle of the page
    front.spacer(150)
    front.add({"type": "figure", "break_inside": "avoid", "object": {
        "type": "group", "box": [0, 0, CONTENT_W, 8],
        "children": [{"type": "rect", "box": [0, 2, 54, 5], "fill": ACCENT,
                      "decorative": True}], "meta": {"no_overlap": False}}})
    front.para(title, style={"font_family": SANS, "font_size": S_H1,
                             "font_weight": 800, "color": INK, "line_height": 1.05})
    front.para(subtitle, style={"font_family": SERIF, "font_size": 12.5,
                                "font_style": "italic", "color": Q_DARK})
    front.spacer(14)
    front.para("Edição em Português (Brasil)", style={
        "font_family": SANS, "font_size": S_CAP, "font_weight": 700,
        "color": ACCENT, "letter_spacing": 1.6})
    front.para("As fontes que fundamentam estas diretrizes estão nomeadas no "
               "texto e reunidas na Bibliografia (Apêndice C).",
               style={**NOTE, "font_size": 9.5})
    front.add({"type": "page_break"})
    front.toc(levels=[1], title="Sumário", leader=".")

    # walk blocks
    story = list(front.story())

    def new_page():
        # break_before is modelled but NOT honoured by the engine — force it
        if not story or story[-1].get("type") != "page_break":
            story.append({"type": "page_break"})

    i, n = 0, len(blocks)
    chapters_done = 0
    while i < n:
        b = blocks[i]
        if b[0] == "h1" and b[1].startswith("Prefácio"):
            body, i = collect(blocks, i + 1)
            new_page()
            f = FlowBuilder(); front_section(f, "Prefácio", body)
            story += f.story()
        elif b[0] == "h1" and b[1].startswith("Como usar"):
            body, i = collect(blocks, i + 1)
            new_page()
            f = FlowBuilder(); front_section(f, "Como usar este livro", body)
            story += f.story()
        elif b[0] == "h1" and b[1].startswith("Parte"):
            body, i = collect(blocks, i + 1)
            new_page()
            f = FlowBuilder(); part_divider(f, b[1], body); story += f.story()
        elif b[0] == "h2" and b[1].startswith("Capítulo"):
            chapters_done += 1
            ctitle = re.sub(r"^Capítulo\s+\d+\s*—\s*", "", b[1])
            cb = ChapterBuilder(chapters_done, ctitle)
            body, i = collect(blocks, i + 1)
            render_chapter(cb, body, plate=PLATES.get(chapters_done))
            new_page()
            story += cb.story()
        elif b[0] == "h1" and b[1].startswith("Apêndice"):
            body, i = collect(blocks, i + 1)
            new_page()
            f = FlowBuilder(); front_section(f, b[1], body)
            story += f.story()
        else:
            i += 1

    restyle_headings(story)
    doc = {"dsl": "FrameGraph", "version": HEAD_VERSION, "title": title,
           "profile": "book", "lang": "pt_BR",
           "meta": {"author": "Edição em Português (Brasil)"},
           "defs": {"masters": {"book": {
               "canvas": {"preset": "A5", "orientation": "portrait"}}}},
           "pages": [{"mode": "flow", "id": "book", "media": "paged",
                      "master": "book", "story": story}]}
    validate_document(doc)
    return doc


def collect(blocks, i):
    """Blocks belonging to the current section: until the next h1/h2 boundary."""
    out, n = [], len(blocks)
    while i < n and blocks[i][0] not in ("h1", "h2"):
        if blocks[i][0] != "hr":
            out.append(blocks[i])
        i += 1
    return out, i


# ---------------------------------------------------------------------------- #
# 7 · Marquee plates — the book demonstrates what it teaches (drawn figures)
# ---------------------------------------------------------------------------- #
def _txt(x, y, w, h, text, *, font=SANS, size=10.0, weight=400, color=INK,
         ls=0.0, align="left", italic=False, va="top"):
    st = {"font_family": font, "font_size": size, "font_weight": weight,
          "color": color, "vertical_align": va}
    if ls:
        st["letter_spacing"] = ls
    if italic:
        st["font_style"] = "italic"
    if align != "left":
        st["align"] = align
    return {"type": "text", "box": [x, y, w, h], "text": text, "style": st}


def _rect(x, y, w, h, fill, *, radius=0.0, stroke=None, decorative=True):
    o = {"type": "rect", "box": [x, y, w, h], "fill": fill}
    if radius:
        o["radius"] = radius
    if stroke:
        o["stroke"] = stroke
    if decorative:
        o["decorative"] = True
    return o


def _plate(children, W, H):
    return {"type": "group", "box": [0, 0, W, H], "children": children,
            "meta": {"no_overlap": False}}


def _eyebrow(x, y, w, text, color=ACCENT):
    return [_rect(x, y + 1, 22, 3, color),
            _txt(x + 30, y - 3, w - 30, 14, text, font=SANS, size=7.6,
                 weight=700, color=color, ls=1.5)]


def plate_type(cb):
    W = CONTENT_W
    c = []
    colw = (W - 14) / 2
    c += [_rect(0, 0, colw, 96, tint(INK, GROUND, 0.955), radius=6),
          _rect(colw + 14, 0, colw, 96, tint(INK, GROUND, 0.955), radius=6),
          _txt(0, 6, colw, 66, "Ag", font=SERIF, size=58, color=INK, align="center"),
          _txt(colw + 14, 6, colw, 66, "Ag", font=SANS, size=58, weight=700,
               color=INK, align="center"),
          _txt(0, 76, colw, 14, "SERIFA · CORPO DE TEXTO", font=SANS, size=7.2,
               weight=700, color=Q_DARK, ls=1.1, align="center"),
          _txt(colw + 14, 76, colw, 14, "SEM SERIFA · TÍTULOS", font=SANS,
               size=7.2, weight=700, color=Q_DARK, ls=1.1, align="center")]
    y = 116
    c += _eyebrow(0, y, W, "ESCALA MODULAR · RAZÃO 1,25")
    y += 20
    for lbl, s in [("25,6", 25.6), ("20,5", 20.5), ("16,4", 16.4),
                   ("13,1", 13.1), ("10,5", 10.5)]:
        c += [_txt(0, y + (s - 10) / 2, 36, 14, lbl, font=SANS, size=8, weight=700,
                   color=Q_MID),
              _txt(42, y, W - 42, s * 1.3, "Ritmo & leitura",
                   font=SERIF, size=s, color=INK)]
        y += s + 9
    y += 6
    c += _eyebrow(0, y, W, "UM PASSO DE HIERARQUIA · PESO, NÃO FAMÍLIA")
    y += 20
    for lbl, wt in [("Regular", 400), ("Semibold", 600), ("Bold", 800)]:
        c += [_txt(0, y, W - 120, 20, "Título de seção", font=SANS, size=15,
                   weight=wt, color=INK),
              _txt(W - 116, y + 3, 116, 14, lbl, font=SANS, size=8.5, weight=700,
                   color=Q_MID, align="right")]
        y += 24
    cb.figure(_plate(c, W, y), caption="Um sistema tipográfico: duas famílias, "
              "uma escala, hierarquia por peso.")


def plate_color(cb):
    W = CONTENT_W
    roles = [("Ação", ACCENT), ("Superfície", tint(INK, GROUND, 0.93)),
             ("Texto", INK), ("Sucesso", FACA), ("Alerta", "#9A6324")]
    n = len(roles)
    gap = 8
    sw = (W - gap * (n - 1)) / n
    c = []
    c += _eyebrow(0, 0, W, "PALETA POR FUNÇÃO — CADA COR COM UM TRABALHO")
    y = 22
    for i, (name, col) in enumerate(roles):
        x = i * (sw + gap)
        c += [_rect(x, y, sw, 46, col, radius=5,
                    stroke=Q_MID if name == "Superfície" else None),
              _txt(x, y + 52, sw, 12, name, font=SANS, size=8.2, weight=700,
                   color=INK),
              _txt(x, y + 63, sw, 11, col.upper(), font=SANS, size=6.8,
                   color=Q_MID)]
    y += 88
    c += _eyebrow(0, y, W, "TESTE DO CINZA — A HIERARQUIA SOBREVIVE SEM COR?")
    y += 22
    for i, (name, col) in enumerate(roles):
        x = i * (sw + gap)
        c += [_rect(x, y, sw, 46, greyscale(col), radius=5,
                    stroke=Q_MID if name == "Superfície" else None)]
    y += 54
    cb.figure(_plate(c, W, y), caption="Uma paleta fechada e o teste do cinza: se "
              "os tons somem juntos, a cor fazia o trabalho da estrutura.")


def plate_grid(cb):
    W = CONTENT_W
    cols, gut = 12, 6
    cw = (W - gut * (cols - 1)) / cols
    c = []
    c += _eyebrow(0, 0, W, "GRID DE 12 COLUNAS · CALHAS CONSTANTES")
    y = 22
    gh = 150
    for i in range(cols):
        x = i * (cw + gut)
        c.append(_rect(x, y, cw, gh, tint(ACCENT, GROUND, 0.86), radius=1))
    # content blocks spanning columns, to show the grid in use
    def span(a, b, yy, hh, col, label):
        x = a * (cw + gut)
        w = (b - a) * (cw + gut) - gut
        return [_rect(x, yy, w, hh, col, radius=3),
                _txt(x + 7, yy + hh / 2 - 6, w - 10, 14, label, font=SANS,
                     size=7.6, weight=700, color=GROUND)]
    c += span(0, 8, y + 12, 40, INK, "conteúdo principal · 8 col")
    c += span(8, 12, y + 12, 40, tint(INK, GROUND, 0.3), "lateral · 4 col")
    c += span(0, 4, y + 62, 34, ACCENT, "card")
    c += span(4, 8, y + 62, 34, ACCENT, "card")
    c += span(8, 12, y + 62, 34, ACCENT, "card")
    c += span(0, 12, y + 106, 30, tint(INK, GROUND, 0.5), "rodapé · 12 col")
    y += gh + 8
    cb.figure(_plate(c, W, y), caption="Uma malha comum: margens, calhas e "
              "colunas dão um chão a cada decisão de layout.")


def plate_hierarchy(cb):
    W = CONTENT_W
    c = []
    c += _eyebrow(0, 0, W, "HIERARQUIA POR TAMANHO, PESO E TOM")
    y = 22
    rows = [("Manchete", 24, 800, INK, SANS),
            ("Subtítulo de apoio", 15, 600, tint(INK, GROUND, 0.18), SANS),
            ("Texto corrido, onde vive o conteúdo e a leitura acontece de fato "
             "em ritmo confortável.", 11, 400, INK, SERIF),
            ("LEGENDA · METADADO SECUNDÁRIO", 8, 700, Q_MID, SANS)]
    for text, s, wt, col, fam in rows:
        c.append(_rect(0, y + 2, 4, s, ACCENT if col == INK and wt >= 800 else
                       tint(INK, GROUND, 0.75)))
        h = s + 4 if fam == SANS else s * 2 + 6
        c.append(_txt(14, y, W - 14, h, text, font=fam, size=s, weight=wt,
                      color=col, ls=1.4 if s == 8 else 0))
        y += h + 12
    cb.figure(_plate(c, W, y), caption="Quatro níveis, cada um a um passo do "
              "seguinte: o olho encontra a ordem antes de ler.")


def plate_components(cb):
    W = CONTENT_W
    c = []
    c += _eyebrow(0, 0, W, "COMPONENTES · ESTADOS E TOKENS COMPARTILHADOS")
    y = 20
    # buttons row
    c += [_rect(0, y, 92, 30, ACCENT, radius=6),
          _txt(0, y + 9, 92, 14, "Salvar", font=SANS, size=10.5, weight=700,
               color=GROUND, align="center"),
          _rect(104, y, 92, 30, GROUND, radius=6, stroke=INK),
          _txt(104, y + 9, 92, 14, "Cancelar", font=SANS, size=10.5, weight=600,
               color=INK, align="center"),
          _txt(212, y + 9, 100, 14, "Saiba mais →", font=SANS, size=10.5,
               weight=700, color=ACCENT)]
    y += 44
    # text field
    c += [_txt(0, y, W, 12, "E-MAIL", font=SANS, size=7.4, weight=700,
               color=Q_DARK, ls=1.2),
          _rect(0, y + 16, W, 32, GROUND, radius=6, stroke=Q_MID),
          _txt(12, y + 26, W - 24, 14, "voce@empresa.com", font=SERIF, size=11,
               color=Q_MID)]
    y += 62
    # card with chip
    ch2 = 96
    c += [_rect(0, y, W, ch2, GROUND, radius=8, stroke=tint(INK, GROUND, 0.82)),
          _rect(16, y + 16, 78, 18, tint(FACA, GROUND, 0.8), radius=9),
          _txt(16, y + 19, 78, 12, "ATIVO", font=SANS, size=7.4, weight=700,
               color=FACA, ls=1.0, align="center"),
          _txt(16, y + 42, W - 32, 18, "Plano Empresarial", font=SANS, size=14,
               weight=700, color=INK),
          _txt(16, y + 63, W - 32, 24, "Um cartão reúne rótulo, título e ação "
               "num bloco reutilizável e consistente.", font=SERIF, size=9.5,
               color=Q_DARK)]
    y += ch2 + 6
    cb.figure(_plate(c, W, y), caption="Botões, campo e cartão: um componente é "
              "um bloco nomeado, com todos os estados previstos.")


def plate_chart(cb):
    W = CONTENT_W
    data = [("Busca", 78), ("Navegação direta", 61), ("Recomendação", 44),
            ("Campanha", 29), ("Referência externa", 17)]
    c = []
    c += _eyebrow(0, 0, W, "UM ACENTO, ROTULAGEM DIRETA, ORDEM POR VALOR")
    y = 22
    labw, valw = 118, 34
    barmax = W - labw - valw - 8
    top = max(v for _, v in data)
    bh, gap = 20, 12
    for i, (name, v) in enumerate(data):
        by = y + i * (bh + gap)
        col = ACCENT if i == 0 else tint(INK, GROUND, 0.62)
        bw = barmax * v / top
        c += [_txt(0, by + 3, labw - 8, 14, name, font=SANS, size=9,
                   weight=700 if i == 0 else 400,
                   color=INK if i == 0 else Q_DARK, align="right"),
              _rect(labw, by, bw, bh, col, radius=3),
              _txt(labw + bw + 6, by + 3, valw, 14, f"{v}%", font=SANS, size=9,
                   weight=700, color=col)]
    y += len(data) * (bh + gap) + 2
    cb.figure(_plate(c, W, y), caption="Um bom gráfico: barras ordenadas, um só "
              "acento na origem que importa, valores rotulados na própria barra.")


PLATES = {4: plate_type, 5: plate_color, 6: plate_grid,
          7: plate_hierarchy, 8: plate_components, 14: plate_chart}


def render_pdf(doc, out_path=OUTPUT_PDF, width_pt=A5_WIDTH_PT):
    """Render the flow document to per-page SVGs and assemble one A5 PDF."""
    import cairosvg
    from pypdf import PdfReader, PdfWriter

    svgs = render_page_svgs(doc)
    writer = PdfWriter()
    for svg in svgs:
        reader = PdfReader(io.BytesIO(cairosvg.svg2pdf(bytestring=svg.encode("utf-8"))))
        for page in reader.pages:
            page.scale_by(width_pt / float(page.mediabox.width))  # lossless vector scale
            writer.add_page(page)
    writer.add_metadata({
        "/Title": "Diretrizes de Design 2027",
        "/Author": "Edição em Português (Brasil)",
        "/Subject": "Princípios, padrões e sistemas para produtos digitais"})
    with open(out_path, "wb") as fh:
        writer.write(fh)
    return len(writer.pages)


if __name__ == "__main__":
    document = build()
    print("BUILD OK — story flowables:", len(document["pages"][0]["story"]))
    n = render_pdf(document)
    print(f"PDF OK — {n} pages -> {OUTPUT_PDF}")
