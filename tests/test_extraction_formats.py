from doc_ray.adapters import extraction


def _zip_bytes(files: dict[str, str]) -> bytes:
    import io
    import zipfile

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return out.getvalue()


def test_extract_html_retains_sanitized_render_html_for_original_view():
    data = b"""
    <html><head><title>ignored</title><script>alert(1)</script></head>
      <body><h1>Heading</h1><p>Hello <b>World</b></p></body>
    </html>
    """

    extracted = extraction.extract_html(data, "sample")

    assert extracted.doc_type == "html"
    assert "Heading" in extracted.full_text
    assert "Hello World" in " ".join(extracted.full_text.split())
    assert (
        extracted.metadata["render_html"] == "<h1>Heading</h1><p>Hello <strong>World</strong></p>"
    )


def test_extract_epub_retains_sanitized_spine_render_html_for_original_view():
    data = _zip_bytes(
        {
            "META-INF/container.xml": (
                '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                '<rootfiles><rootfile full-path="OPS/content.opf"/></rootfiles></container>'
            ),
            "OPS/content.opf": (
                '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                '<item id="c1" href="chapter.xhtml" media-type="application/xhtml+xml"/>'
                '</manifest><spine><itemref idref="c1"/></spine></package>'
            ),
            "OPS/chapter.xhtml": "<html><body><h1>Chapter</h1><p>Visible text</p></body></html>",
        }
    )

    extracted = extraction.extract_epub(data, "book")

    assert extracted.doc_type == "epub"
    assert "Chapter" in extracted.full_text
    assert extracted.metadata["render_html"] == "<h1>Chapter</h1><p>Visible text</p>"


def test_extract_svg_retains_sanitized_text_render_html_for_original_view():
    data = b"""
    <svg xmlns="http://www.w3.org/2000/svg">
      <script>alert(1)</script>
      <text>Visible <tspan>Label</tspan></text>
    </svg>
    """

    extracted = extraction.extract_svg(data, "diagram")

    assert extracted.doc_type == "svg"
    assert "Visible Label" in " ".join(extracted.full_text.split())
    assert extracted.metadata["render_html"] == "<p>Visible Label</p>"


def test_extract_docx_retains_paragraph_render_html_for_original_view():
    import io

    import pytest

    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("First paragraph")
    document.add_paragraph("Second paragraph")
    out = io.BytesIO()
    document.save(out)

    extracted = extraction.extract_docx(out.getvalue(), "sample")

    assert extracted.doc_type == "docx"
    assert extracted.full_text == "First paragraph\nSecond paragraph"
    assert extracted.metadata["render_html"] == ("<p>First paragraph</p><p>Second paragraph</p>")
