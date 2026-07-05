"""TDD for the PDF layout → Markdown logic (ingest.pdf_layout).

These test the pure functions that turn PyMuPDF's span geometry + font sizes
into structured Markdown — no PDF or PyMuPDF needed here; synthetic spans stand
in for what fitz yields. (A separate module integration-tests real PDFs.)
"""

from __future__ import annotations

import pytest

from doc_ray.domain import pdf_layout as pl

# --- builders --------------------------------------------------------------


def span(text, x0=0.0, x1=None, size=10.0, bold=False):
    return {
        "text": text,
        "x0": x0,
        "x1": x1 if x1 is not None else x0 + len(text) * size * 0.5,
        "size": size,
        "bold": bold,
    }


def line(spans, y0=100.0, y1=None, page=0, page_h=800.0):
    return {
        "spans": spans,
        "y0": y0,
        "y1": y1 if y1 is not None else y0 + 12,
        "page": page,
        "page_h": page_h,
    }


def blk(lines):
    return {"lines": lines}


# --- line_text: spacing from span gaps -------------------------------------


def test_line_text_inserts_space_on_wide_gap():
    s = [span("y=M(x)", 10, 60, size=10), span("denote", 75, 120, size=10)]
    assert pl.line_text(s) == "y=M(x) denote"


def test_line_text_keeps_tight_kerning_together():
    s = [span("hel", 10, 25, size=10), span("lo", 25.4, 35, size=10)]
    assert pl.line_text(s) == "hello"


def test_line_text_does_not_double_existing_space():
    s = [span("hello ", 10, 45, size=10), span("world", 70, 110, size=10)]
    assert pl.line_text(s) == "hello world"


# --- heading_level ---------------------------------------------------------


@pytest.mark.parametrize(
    "size, body, lvl",
    [
        (20, 10, 1),
        (15, 10, 1),
        (13, 10, 2),
        (12.5, 10, 2),
        (11.5, 10, 3),
        (10.6, 10, 0),
        (10, 10, 0),
    ],
)
def test_heading_level(size, body, lvl):
    assert pl.heading_level(size, body) == lvl


def test_heading_level_uses_corpus_specific_thresholds():
    thresholds = pl.HeadingThresholds(level1_ratio=1.8, level2_ratio=1.5, level3_ratio=1.25)

    assert pl.heading_level(15, 10, thresholds) == 2
    assert pl.heading_level(12, 10, thresholds) == 0


# --- bullets ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("• item", True),
        ("•Mbe", True),
        ("- x", True),
        ("* z", True),
        ("– y", True),
        ("normal text", False),
        ("3.1 Heading", False),
    ],
)
def test_is_bullet(text, expected):
    assert pl.is_bullet(text) is expected


def test_strip_bullet():
    assert pl.strip_bullet("• item") == "item"
    assert pl.strip_bullet("•Mbe") == "Mbe"
    assert pl.strip_bullet("-  x") == "x"


# --- classify_line ---------------------------------------------------------


def test_classify_heading_by_font_size():
    assert pl.classify_line("Formal Statement", size=18, bold=False, body=10) == ("heading", 1)


def test_classify_heading_by_bold_short_line():
    assert pl.classify_line("Definitions", size=10, bold=True, body=10) == ("heading", 3)


def test_classify_bold_heading_respects_tuned_word_limit():
    thresholds = pl.HeadingThresholds(bold_max_words=1)

    assert pl.classify_line("Two Words", size=10, bold=True, body=10, thresholds=thresholds) == (
        "para",
        0,
    )


def test_classify_bullet_is_list():
    assert pl.classify_line("• first point", size=10, bold=False, body=10) == ("list", 0)


def test_classify_plain_sentence_is_paragraph():
    assert pl.classify_line(
        "This is a normal sentence that ends.", size=10, bold=False, body=10
    ) == ("para", 0)


def test_classify_bold_sentence_is_still_paragraph():
    # Bold but ends like a sentence -> body text, not a heading.
    assert pl.classify_line(
        "This whole clause is emphasised and it ends.", size=10, bold=True, body=10
    ) == ("para", 0)


# --- de-hyphenation --------------------------------------------------------


def test_dehyphenate_joins_split_word():
    assert pl.dehyphenate_join("adversari-", "ally") == "adversarially"


def test_join_plain_lines_with_space():
    assert pl.dehyphenate_join("hello", "world") == "hello world"


def test_dash_after_space_is_not_hyphenation():
    assert pl.dehyphenate_join("well -", "known") == "well - known"


# --- body_size -------------------------------------------------------------


def test_body_size_is_the_dominant_text_size():
    blocks = [
        blk([line([span("plenty of body text here", size=10)])]),
        blk([line([span("and yet more body text", size=10)])]),
        blk([line([span("BIG", size=24)])]),
    ]
    assert pl.body_size(blocks) == 10


# --- header/footer stripping ----------------------------------------------


def test_strip_marginals_removes_recurring_footer():
    blocks = []
    for p in range(3):
        blocks.append(
            blk([line([span(f"Report Page {p + 1}", size=8)], y0=782, y1=792, page=p, page_h=800)])
        )  # footer band
        blocks.append(
            blk(
                [
                    line(
                        [span("Real body content here.", size=10)],
                        y0=300,
                        y1=312,
                        page=p,
                        page_h=800,
                    )
                ]
            )
        )  # mid page
    kept = pl.strip_marginals(blocks)
    texts = [pl.line_text(ln["spans"]) for b in kept for ln in b["lines"]]
    assert not any("Report Page" in t for t in texts)
    assert any("Real body content" in t for t in texts)


def test_strip_marginals_keeps_unique_marginal_text():
    # A one-off line in the footer band (not recurring) is content, keep it.
    blocks = [
        blk([line([span("A unique closing remark.", size=10)], y0=785, y1=797, page=0, page_h=800)])
    ]
    kept = pl.strip_marginals(blocks)
    assert any(
        "unique closing remark" in pl.line_text(ln["spans"]) for b in kept for ln in b["lines"]
    )


# --- assembly to Markdown --------------------------------------------------


def test_to_markdown_assembles_headings_lists_and_paragraphs():
    blocks = [
        blk([line([span("Formal Statement", size=18)])]),
        blk([line([span("It is a plain paragraph of body text.", size=10)])]),
        blk([line([span("• first", size=10)]), line([span("• second", size=10)])]),
    ]
    md = pl.to_markdown(blocks)
    assert "# Formal Statement" in md
    assert "It is a plain paragraph of body text." in md
    assert "- first" in md
    assert "- second" in md
    # heading and paragraph are separated by a blank line
    assert "\n\n" in md


def test_sanitize_text_strips_nul_and_controls_keeps_whitespace():
    assert pl.sanitize_text("a\x00b") == "ab"  # NUL removed (Postgres can't store it)
    assert pl.sanitize_text("x\x0cy") == "xy"  # form feed removed
    assert pl.sanitize_text("line1\nline2\tcol") == "line1\nline2\tcol"  # \n \t kept


def test_to_markdown_output_never_contains_nul():
    blocks = [blk([line([span("a\x00b of body text here now", size=10)])])]
    assert "\x00" not in pl.to_markdown(blocks)


def test_to_markdown_reflows_and_dehyphenates_within_paragraph():
    blocks = [
        blk(
            [
                line([span("a long paragraph that wraps and is hyphen-", size=10)], y0=100),
                line([span("ated across the line boundary here.", size=10)], y0=114),
            ]
        )
    ]
    md = pl.to_markdown(blocks)
    assert "hyphenated across" in md
