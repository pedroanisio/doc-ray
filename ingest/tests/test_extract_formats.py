"""Regression tests: the ingester extracts every supported document format."""

from __future__ import annotations

import io
import zipfile

import pytest

from ingest.extract import _EXTRACTORS, extract


def test_all_formats_registered():
    for ext in (".pdf", ".txt", ".text", ".md", ".markdown",
                ".html", ".htm", ".epub", ".docx"):
        assert ext in _EXTRACTORS, ext


def test_plain_text():
    e = extract(b"Hello world. Bye.", "note", ".txt")
    assert e.doc_type == "text" and e.extractor == "utf8"
    assert e.full_text == "Hello world. Bye."


def test_markdown():
    e = extract(b"# Title\n\nSome body text.", "doc", ".md")
    assert "Some body text." in e.full_text


def test_html_strips_scripts_and_tags():
    html = (b"<html><head><title>t</title></head><body>"
            b"<p>Hi there.</p><script>x=1</script><p>Second.</p></body></html>")
    e = extract(html, "page", ".html")
    assert e.doc_type == "html"
    assert "Hi there." in e.full_text and "Second." in e.full_text
    assert "x=1" not in e.full_text and "<p>" not in e.full_text   # tags/scripts gone


def _minimal_epub() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            '<manifest><item id="c2" href="ch2.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>')
        z.writestr("OEBPS/ch1.xhtml",
                   '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>First chapter.</p></body></html>')
        z.writestr("OEBPS/ch2.xhtml",
                   '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Second chapter.</p></body></html>')
    return buf.getvalue()


def test_epub_reads_in_spine_order():
    e = extract(_minimal_epub(), "book", ".epub")
    assert e.doc_type == "epub"
    assert "First chapter." in e.full_text and "Second chapter." in e.full_text
    # spine puts ch1 before ch2 even though the manifest lists ch2 first
    assert e.full_text.index("First chapter.") < e.full_text.index("Second chapter.")


def test_unsupported_format_raises():
    with pytest.raises(ValueError):
        extract(b"data", "f", ".xyz")
