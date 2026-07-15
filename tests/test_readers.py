"""Tests for the document readers and the extension dispatch."""

from __future__ import annotations

import sys

import extract_phrases as ep
import pytest

# ---------------------------------------------------------------------------
# read_txt
# ---------------------------------------------------------------------------


def test_read_txt_round_trips_utf8(tmp_path):
    p = tmp_path / "note.txt"
    p.write_text("Olá, mundo. Ação!", encoding="utf-8")
    assert ep.read_txt(p) == "Olá, mundo. Ação!"


# ---------------------------------------------------------------------------
# HTML text extraction
# ---------------------------------------------------------------------------


def test_html_strips_tags_and_keeps_visible_text():
    text = ep._html_to_text("<p>Hello</p><p>World</p>")
    assert "Hello" in text and "World" in text
    assert "<p>" not in text


def test_html_skips_script_and_style_content():
    text = ep._html_to_text("<p>Keep</p><script>evil()</script><style>.x{}</style><p>This</p>")
    assert "Keep" in text and "This" in text
    assert "evil" not in text and ".x{" not in text


def test_html_block_tags_insert_line_breaks():
    # <p> boundaries become newlines, so the two blocks are separable.
    text = ep._html_to_text("<p>First</p><p>Second</p>")
    assert "First" in text and "Second" in text
    assert "\n" in text.strip()


def test_html_decodes_entities():
    assert "Ben & Jerry" in ep._html_to_text("<p>Ben &amp; Jerry</p>")


def test_read_html_reads_from_disk(tmp_path):
    p = tmp_path / "page.html"
    p.write_text("<h1>Title</h1><p>Body text.</p>", encoding="utf-8")
    text = ep.read_html(p)
    assert "Title" in text and "Body text." in text


# ---------------------------------------------------------------------------
# EPUB reading
# ---------------------------------------------------------------------------


def test_epub_reads_chapters_in_spine_order(make_epub):
    # Spine reverses dict order; the reader must honour the spine, not the zip.
    path = make_epub(
        {"c1.xhtml": "<p>Alpha chapter.</p>", "c2.xhtml": "<p>Beta chapter.</p>"},
        spine=["c2.xhtml", "c1.xhtml"],
    )
    text = ep.read_epub(path)
    assert text.index("Beta") < text.index("Alpha")


def test_epub_falls_back_to_sorted_files_without_container(make_epub):
    path = make_epub(
        {"b.xhtml": "<p>Bravo.</p>", "a.xhtml": "<p>Alpha.</p>"},
        include_container=False,
    )
    text = ep.read_epub(path)
    # Fallback sorts by filename: a.xhtml before b.xhtml.
    assert text.index("Alpha") < text.index("Bravo")


def test_epub_falls_back_on_broken_container(make_epub):
    path = make_epub(
        {"only.xhtml": "<p>Content here.</p>"},
        container_body="<container>not valid xml pointer</container>",
    )
    assert "Content here." in ep.read_epub(path)


# ---------------------------------------------------------------------------
# extract_text — dispatch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["doc.txt", "DOC.TXT", "readme.md", "notes.text"])
def test_extract_text_reads_plaintext_formats(tmp_path, name):
    p = tmp_path / name
    p.write_text("Plain content.", encoding="utf-8")
    assert ep.extract_text(p) == "Plain content."


def test_extract_text_html_is_stripped(tmp_path):
    p = tmp_path / "x.HTML"  # case-insensitive suffix
    p.write_text("<p>Hi there.</p>", encoding="utf-8")
    assert "Hi there." in ep.extract_text(p)
    assert "<p>" not in ep.extract_text(p)


def test_extract_text_unsupported_extension_exits(tmp_path):
    p = tmp_path / "thing.rtf"
    p.write_text("whatever", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        ep.extract_text(p)
    assert ".rtf" in str(exc.value)


# ---------------------------------------------------------------------------
# Optional-dependency error paths (no real pypdf/python-docx needed)
# ---------------------------------------------------------------------------


def test_read_pdf_without_pypdf_exits(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pypdf", None)  # force ImportError
    with pytest.raises(SystemExit) as exc:
        ep.read_pdf(tmp_path / "x.pdf")
    assert "pypdf" in str(exc.value)


def test_read_docx_without_python_docx_exits(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "docx", None)
    with pytest.raises(SystemExit) as exc:
        ep.read_docx(tmp_path / "x.docx")
    assert "python-docx" in str(exc.value)
