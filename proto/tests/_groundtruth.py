"""Ad-hoc ground-truth probe (not a test). Run directly to observe behavior:
    .venv-proto/bin/python proto/tests/_groundtruth.py
"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE.parent), str(_HERE)]

import fitz
import headings
from synthpdf import L, PdfSpec, body_block, build_pdf, heading, open_doc

TMP = _HERE / "_gt_tmp"
TMP.mkdir(exist_ok=True)


def show(name, path):
    doc = open_doc(str(path))
    toc = doc.get_toc(simple=True)
    src = "toc" if (toc and len({e[2] for e in toc}) > 1) else "heuristic"
    hs = headings.extract_headings(doc)
    print(f"\n=== {name}  (pages={doc.page_count}, source={src}, n={len(hs)}) ===")
    for h in hs:
        print(f"  L{h.level} conf={h.confidence} p{h.page+1}  {h.text!r}")


def build(name, lines, toc=None):
    return build_pdf(str(TMP / f"{name}.pdf"), PdfSpec(lines=list(lines), toc=toc))


# TOC fast-path
show("toc_pdf", build("toc",
    body_block(3)
    + [L("Chapter One", 18, True, new_page=True)] + body_block(4)
    + [L("Section 1.1", 14, True, new_page=True)] + body_block(4)
    + [L("Chapter Two", 18, True, new_page=True)] + body_block(4),
    toc=[[1, "Chapter One", 2], [2, "Section 1.1", 3], [1, "Chapter Two", 4]]))

# numbered
show("numbered_pdf", build("numbered",
    body_block(3)
    + [heading("1 Introduction")] + body_block(5)
    + [heading("1.1 Background")] + body_block(5)
    + [heading("1.1.1 Prior Work")] + body_block(5)
    + [heading("2 Methods")] + body_block(5)))

# wrapped
show("wrapped_title_pdf", build("wrapped",
    [L("A Very Long Structural Heading That Continues", 16, True, gap_before=18),
     L("onto a second visual line without ending", 16, True, gap_before=2)]
    + body_block(6) + [heading("2 Next Section")] + body_block(4)))

# running heads
rh = []
for p in range(4):
    rh.append(L(f"Confidential Report Page {p+1}", 10.0, False, new_page=(p > 0)))
    if p == 0: rh.append(heading("1 Real Heading One"))
    if p == 2: rh.append(heading("2 Real Heading Two"))
    rh += body_block(3, start=p * 10)
show("running_head_pdf", build("runhead", rh))

# printed toc
show("printed_toc_pdf", build("printedtoc",
    [L("TABLE OF CONTENTS", 12, True, gap_before=10)]
    + [L(f"{n:03d} - Chapter {i}") for i, n in enumerate((6, 44, 63, 81), 1)]
    + [heading("1 Actual Heading", 16, True)] + body_block(5)))

# junk
show("junk_pdf", build("junk",
    body_block(3)
    + [L("E", 11, True, gap_before=8), L("1 0", 11, True, gap_before=8),
       L("7", 20, True, gap_before=8)]
    + [heading("1 Genuine Heading")] + body_block(4)))

# body false positive
long_line = ("8 conjugados distintos todos com parte real igual a dez e "
             "portanto o resultado permanece constante ao longo do texto = 10")
show("body_false_positive_pdf", build("bodyfp",
    body_block(3) + [L(long_line)] + body_block(3)
    + [heading("1 The Only Real Heading")] + body_block(3)))
