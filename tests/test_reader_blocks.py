"""Tests for reader_blocks structural-splitting helpers.

These exercise ``split_structural_blocks`` directly (pure text -> offsets),
independent of the Flask route and DB layer covered in test_webapp.py.
"""

from __future__ import annotations

from webapp.reader_blocks import (
    render_annotated_blocks,
    render_plain_blocks,
    split_structural_blocks,
)


def _slices(text, blocks):
    return [(b["type"], text[b["start"] : b["end"]]) for b in blocks]


def test_plain_sentence_is_a_single_para_block():
    text = "Automobile designers are responsible for the styling of the vehicle."
    blocks = split_structural_blocks(text)
    assert _slices(text, blocks) == [("para", text)]


def test_leading_heading_strips_marker_like_before():
    for marker, kind in [("#", "h1"), ("##", "h2"), ("###", "h3")]:
        text = f"{marker} Chapter One"
        blocks = split_structural_blocks(text)
        assert _slices(text, blocks) == [(kind, "Chapter One")]


def test_embedded_headings_mid_sentence_are_split_out():
    text = "Sketches by the faculty ## WHY THIS COURSE? ## WHO IS THIS FOR?"
    blocks = split_structural_blocks(text)
    assert _slices(text, blocks) == [
        ("para", "Sketches by the faculty "),
        ("h2", "WHY THIS COURSE? "),
        ("h2", "WHO IS THIS FOR?"),
    ]


def test_mixed_heading_levels_mid_sentence():
    text = "Intro ## Stages ### Sketching more"
    blocks = split_structural_blocks(text)
    assert _slices(text, blocks) == [
        ("para", "Intro "),
        ("h2", "Stages "),
        ("h3", "Sketching more"),
    ]


def test_leading_bullet_run_becomes_list_items():
    text = "- Learn skills - Make yourself ready - Pursue design as a hobby"
    blocks = split_structural_blocks(text)
    assert _slices(text, blocks) == [
        ("list", "Learn skills "),
        ("list", "Make yourself ready "),
        ("list", "Pursue design as a hobby"),
    ]


def test_lone_mid_sentence_dash_is_left_as_prose():
    # A single " - " aside reads as an em-dash in ordinary prose, not a list.
    text = "The result - as expected - was correct."
    blocks = split_structural_blocks(text)
    assert _slices(text, blocks) == [("para", text)]


def test_lead_in_text_before_a_bullet_run_stays_a_paragraph():
    # Three-plus markers reads confidently as a bullet run even with a label
    # in front of it; two markers is left alone (see the em-dash test above)
    # since that shape is just as often a mid-sentence aside.
    text = "Pre requisites: - passion for automobiles - basic sketching - a laptop"
    blocks = split_structural_blocks(text)
    assert _slices(text, blocks) == [
        ("para", "Pre requisites: "),
        ("list", "passion for automobiles "),
        ("list", "basic sketching "),
        ("list", "a laptop"),
    ]


def test_empty_text_yields_single_empty_para_block():
    assert split_structural_blocks("") == [{"type": "para", "start": 0, "end": 0}]


def test_render_plain_blocks_slices_text_per_block():
    text = "## Title body text"
    rendered = render_plain_blocks(text, split_structural_blocks(text))
    assert rendered == [{"type": "h2", "text": "Title body text"}]


def test_render_annotated_blocks_rebases_occurrences_per_block():
    body = "## Title said hello"
    # "said" occupies body[9:13] relative to the whole body string.
    occs = [{"rel_start": 9, "rel_end": 13, "uid": "lx_1", "lemma": "say", "pos": "VERB"}]
    rendered = render_annotated_blocks(body, occs, split_structural_blocks(body))
    assert len(rendered) == 1
    assert rendered[0]["type"] == "h2"
    html = str(rendered[0]["html"])
    assert 'class="w p-VERB"' in html
    assert "said" in html


# --- blank-line boundaries (the real assembly seams from pdf_layout.to_markdown) ---
#
# Everything above exercises the marker-counting fallback, used once real
# newlines are gone (plain-mode's whitespace-collapsed ``sentence.text``, or
# a chunk that's already down to one line). ``body`` in annotated mode is a
# raw ``full_text`` slice, so it still has the original blank-line
# (``\n\n``) seams between Markdown blocks and single-newline seams between
# bullet lines — a strictly more reliable signal, preferred whenever present.


def test_blank_line_separated_heading_and_paragraph():
    text = "# Automotive Design\n\nEvery automobile starts with a simple idea."
    blocks = split_structural_blocks(text)
    assert _slices(text, blocks) == [
        ("h1", "Automotive Design"),
        ("para", "Every automobile starts with a simple idea."),
    ]


def test_blank_line_heading_does_not_swallow_the_next_unmarked_paragraph():
    # The exact failure mode from the wild: a heading block glommed together
    # with several caption/paragraph blocks that lack their own "#" prefix,
    # because sentence segmentation only splits on terminal punctuation.
    text = (
        "### Stages In Automobile Design\n\n"
        "Every automobile starts with a simple idea.\n\n"
        "Sketches by the faculty\n\n"
        "### Sketching / Ideation"
    )
    blocks = split_structural_blocks(text)
    assert _slices(text, blocks) == [
        ("h3", "Stages In Automobile Design"),
        ("para", "Every automobile starts with a simple idea."),
        ("para", "Sketches by the faculty"),
        ("h3", "Sketching / Ideation"),
    ]


def test_blank_line_delimited_list_block_splits_on_single_newlines():
    text = (
        "OUR VISION\n\n"
        "- Learn skills to prepare your own portfolio\n"
        "- Make yourself ready to apply for design education\n"
        "- Pursue automotive design as a hobby\n\n"
        "WHY THIS COURSE?"
    )
    blocks = split_structural_blocks(text)
    assert _slices(text, blocks) == [
        ("para", "OUR VISION"),
        ("list", "Learn skills to prepare your own portfolio"),
        ("list", "Make yourself ready to apply for design education"),
        ("list", "Pursue automotive design as a hobby"),
        ("para", "WHY THIS COURSE?"),
    ]


def test_blank_line_chunk_with_no_markers_stays_a_paragraph():
    text = "Sketches by the faculty\n\nSketches by the faculty\n\nSketches by the faculty"
    blocks = split_structural_blocks(text)
    assert [t for t, _ in _slices(text, blocks)] == ["para", "para", "para"]
