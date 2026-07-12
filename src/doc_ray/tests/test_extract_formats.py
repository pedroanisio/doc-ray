"""Regression tests: the ingester extracts every supported document format."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import types
import zipfile
from collections.abc import Callable, Mapping

import pytest

from doc_ray.adapters import extraction
from doc_ray.adapters.extraction import _EXTRACTORS, extract

GoldenCase = tuple[Callable[[], bytes], str, str]


def _missing_golden_extensions(registered: Mapping[str, object]) -> list[str]:
    """Return registered extractor extensions that lack a golden case."""
    return sorted(set(registered) - set(_GOLDEN_FORMATS))


def test_extractor_coverage_guard_detects_uncovered_extension():
    registered = {**_EXTRACTORS, ".odt": object()}

    assert _missing_golden_extensions(registered) == [".odt"]


def test_all_registered_extractors_have_golden_fixture_case():
    assert _missing_golden_extensions(_EXTRACTORS) == []


def _bytes(value: bytes) -> Callable[[], bytes]:
    return lambda: value


def _zip_bytes(files: Mapping[str, str]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return out.getvalue()


def _minimal_pdf() -> bytes:
    fitz = pytest.importorskip("fitz", reason="PyMuPDF not installed")
    doc = fitz.open()
    page = doc.new_page(width=300, height=200)
    page.insert_text((40, 80), "Golden PDF text.", fontsize=12)
    return doc.tobytes()


def _minimal_docx() -> bytes:
    docx = pytest.importorskip("docx", reason="python-docx not installed")
    document = docx.Document()
    document.add_paragraph("Golden DOCX text.")
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def _minimal_pptx() -> bytes:
    pptx = pytest.importorskip("pptx", reason="python-pptx not installed")
    presentation = pptx.Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[0])
    slide.shapes.title.text = "Golden PPTX title."
    slide.placeholders[1].text = "Golden PPTX body."
    out = io.BytesIO()
    presentation.save(out)
    return out.getvalue()


def test_plain_text():
    e = extract(b"Hello world. Bye.", "note", ".txt")
    assert e.doc_type == "text" and e.extractor == "utf8"
    assert e.full_text == "Hello world. Bye."


def test_markdown():
    e = extract(b"# Title\n\nSome body text.", "doc", ".md")
    assert "Some body text." in e.full_text


def test_html_strips_scripts_and_tags():
    html = (
        b"<html><head><title>t</title></head><body>"
        b"<p>Hi there.</p><script>x=1</script><p>Second.</p></body></html>"
    )
    e = extract(html, "page", ".html")
    assert e.doc_type == "html"
    assert "Hi there." in e.full_text and "Second." in e.full_text
    assert "x=1" not in e.full_text and "<p>" not in e.full_text  # tags/scripts gone


def test_html_emits_markdown_headings_and_list_items():
    html = (
        b"<html><body><h1>Document Title</h1><p>Lead paragraph.</p>"
        b"<h2>Findings</h2><ul><li>First finding</li><li>Second finding</li></ul>"
        b"</body></html>"
    )

    e = extract(html, "structured-html", ".html")

    assert e.doc_type == "html"
    assert e.full_text.splitlines() == [
        "# Document Title",
        "Lead paragraph.",
        "## Findings",
        "- First finding",
        "- Second finding",
    ]


def _minimal_svg() -> bytes:
    return b"""<?xml version="1.0" encoding="UTF-8"?>
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100">
      <title>Fixture diagram</title>
      <text x="10" y="20">Golden SVG text.</text>
      <text x="10" y="42"><tspan>Nested tspan label.</tspan></text>
    </svg>"""


def test_svg_extracts_text_and_tspan_elements():
    e = extract(_minimal_svg(), "diagram", ".svg")

    assert e.doc_type == "svg"
    assert e.content_type == "image/svg+xml"
    assert e.title == "Fixture diagram"
    assert "Golden SVG text." in e.full_text
    assert "Nested tspan label." in e.full_text


def test_svg_extracts_native_coordinate_page_runs():
    e = extract(_minimal_svg(), "diagram", ".svg")

    assert e.page_runs == [
        {
            "page": 0,
            "page_width": 200.0,
            "page_height": 100.0,
            "bbox": (10.0, 8.0, 125.2, 20.0),
            "text": "Golden SVG text.",
        },
        {
            "page": 0,
            "page_width": 200.0,
            "page_height": 100.0,
            "bbox": (10.0, 30.0, 146.8, 42.0),
            "text": "Nested tspan label.",
        },
    ]


def test_svg_extension_is_supported_for_upload_content_type():
    assert extraction.is_supported_extension(".svg")
    assert extraction.content_type_for_extension(".svg") == "image/svg+xml"


def test_svg_emits_markdown_headings_from_font_size_and_weight():
    svg = b"""
    <svg xmlns="http://www.w3.org/2000/svg">
      <text style="font-size:24px;font-weight:700">Primary SVG Title</text>
      <text font-size="12">Body line one.</text>
      <text style="font-size:12px">Body line two.</text>
      <text style="font-size:14px;font-weight:bold">Short Bold Label</text>
    </svg>
    """

    e = extract(svg, "diagram", ".svg")

    assert e.doc_type == "svg"
    assert e.full_text.splitlines() == [
        "# Primary SVG Title",
        "Body line one.",
        "Body line two.",
        "### Short Bold Label",
    ]


def test_docx_emits_markdown_headings_and_list_items(monkeypatch):
    class _Style:
        def __init__(self, name: str) -> None:
            self.name = name

    class _Paragraph:
        def __init__(self, text: str, style: str) -> None:
            self.text = text
            self.style = _Style(style)

    class _Document:
        paragraphs = [
            _Paragraph("Document Title", "Heading 1"),
            _Paragraph("Lead paragraph.", "Normal"),
            _Paragraph("Findings", "Heading 2"),
            _Paragraph("First finding", "List Bullet"),
            _Paragraph("Second finding", "List Number"),
            _Paragraph("Appendix", "Heading 3"),
        ]

    fake_docx = types.SimpleNamespace(Document=lambda stream: _Document())
    monkeypatch.setitem(sys.modules, "docx", fake_docx)

    e = extract(_zip_bytes({"word/document.xml": "<w:document/>"}), "structured-docx", ".docx")

    assert e.doc_type == "docx"
    assert e.full_text.splitlines() == [
        "# Document Title",
        "Lead paragraph.",
        "## Findings",
        "- First finding",
        "- Second finding",
        "### Appendix",
    ]


def test_pptx_extension_is_supported_for_upload_content_type():
    assert extraction.is_supported_extension(".pptx")
    assert (
        extraction.content_type_for_extension(".pptx")
        == "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )


def test_pptx_extracts_slide_text_structure_and_speaker_notes(monkeypatch):
    class _Paragraph:
        def __init__(self, text: str, level: int = 0) -> None:
            self.text = text
            self.level = level

    class _TextFrame:
        def __init__(self, paragraphs: list[_Paragraph]) -> None:
            self.paragraphs = paragraphs

    class _PlaceholderFormat:
        def __init__(self, kind: str) -> None:
            self.type = kind

    class _Shape:
        def __init__(
            self,
            paragraphs: list[_Paragraph],
            *,
            placeholder_type: str | None = None,
            left: int = 0,
            top: int = 0,
            width: int = 0,
            height: int = 0,
        ) -> None:
            self.has_text_frame = True
            self.text_frame = _TextFrame(paragraphs)
            self.is_placeholder = placeholder_type is not None
            self.placeholder_format = _PlaceholderFormat(placeholder_type or "")
            self.left = left
            self.top = top
            self.width = width
            self.height = height

    class _NotesSlide:
        notes_text_frame = _TextFrame([_Paragraph("Speaker note one.")])

    class _Slide:
        shapes = [
            _Shape(
                [_Paragraph("Deck Title")],
                placeholder_type="TITLE",
                left=100,
                top=200,
                width=300,
                height=50,
            ),
            _Shape(
                [_Paragraph("First bullet", 0), _Paragraph("Nested bullet", 1)],
                left=100,
                top=300,
                width=500,
                height=120,
            ),
        ]
        has_notes_slide = True
        notes_slide = _NotesSlide()

    class _Presentation:
        slide_width = 1000
        slide_height = 750
        slides = [_Slide()]

    fake_pptx = types.SimpleNamespace(Presentation=lambda stream: _Presentation())
    monkeypatch.setitem(sys.modules, "pptx", fake_pptx)

    e = extract(_zip_bytes({"ppt/presentation.xml": "<p:presentation/>"}), "deck", ".pptx")

    assert e.doc_type == "pptx"
    assert (
        e.content_type
        == "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )
    assert e.full_text.splitlines() == [
        "# Deck Title",
        "- First bullet",
        "  - Nested bullet",
        "Speaker note one.",
    ]
    assert e.page_runs == [
        {
            "page": 0,
            "page_width": 1000.0,
            "page_height": 750.0,
            "bbox": (100.0, 200.0, 400.0, 250.0),
            "text": "# Deck Title",
        },
        {
            "page": 0,
            "page_width": 1000.0,
            "page_height": 750.0,
            "bbox": (100.0, 300.0, 600.0, 420.0),
            "text": "- First bullet",
        },
        {
            "page": 0,
            "page_width": 1000.0,
            "page_height": 750.0,
            "bbox": (100.0, 300.0, 600.0, 420.0),
            "text": "  - Nested bullet",
        },
    ]


def _minimal_epub() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        z.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            '<manifest><item id="c2" href="ch2.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>',
        )
        z.writestr(
            "OEBPS/ch1.xhtml",
            '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>First chapter.</p></body></html>',
        )
        z.writestr(
            "OEBPS/ch2.xhtml",
            '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Second chapter.</p></body></html>',
        )
    return buf.getvalue()


_GOLDEN_FORMATS: dict[str, GoldenCase] = {
    ".pdf": (_minimal_pdf, "pdf", "Golden PDF text."),
    ".txt": (_bytes(b"Golden text fixture."), "text", "Golden text fixture."),
    ".text": (_bytes(b"Golden text alias fixture."), "text", "Golden text alias fixture."),
    ".md": (_bytes(b"# Title\n\nGolden Markdown fixture."), "text", "Golden Markdown fixture."),
    ".markdown": (
        _bytes(b"# Title\n\nGolden Markdown alias fixture."),
        "text",
        "Golden Markdown alias fixture.",
    ),
    ".html": (
        _bytes(b"<html><body><p>Golden HTML fixture.</p></body></html>"),
        "html",
        "Golden HTML fixture.",
    ),
    ".htm": (
        _bytes(b"<html><body><p>Golden HTM alias fixture.</p></body></html>"),
        "html",
        "Golden HTM alias fixture.",
    ),
    ".svg": (_minimal_svg, "svg", "Golden SVG text."),
    ".epub": (_minimal_epub, "epub", "First chapter."),
    ".docx": (_minimal_docx, "docx", "Golden DOCX text."),
    ".pptx": (_minimal_pptx, "pptx", "Golden PPTX title."),
}


@pytest.mark.parametrize("extension", sorted(_GOLDEN_FORMATS))
def test_registered_extractor_golden_fixture(extension):
    data_factory, expected_doc_type, expected_text = _GOLDEN_FORMATS[extension]

    extracted = extract(data_factory(), f"golden-{extension.lstrip('.')}", extension)

    assert extracted.doc_type == expected_doc_type
    assert expected_text in extracted.full_text


def test_epub_reads_in_spine_order():
    e = extract(_minimal_epub(), "book", ".epub")
    assert e.doc_type == "epub"
    assert "First chapter." in e.full_text and "Second chapter." in e.full_text
    # spine puts ch1 before ch2 even though the manifest lists ch2 first
    assert e.full_text.index("First chapter.") < e.full_text.index("Second chapter.")


def test_epub_emits_markdown_headings_and_list_items():
    data = _epub_with_documents(
        {
            "OEBPS/ch1.xhtml": (
                '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                "<h1>Chapter Title</h1><p>Opening.</p><ol><li>Step one</li><li>Step two</li></ol>"
                "</body></html>"
            )
        },
        spine=("OEBPS/ch1.xhtml",),
    )

    e = extract(data, "structured-book", ".epub")

    assert e.doc_type == "epub"
    assert e.full_text.splitlines() == [
        "# Chapter Title",
        "Opening.",
        "- Step one",
        "- Step two",
    ]


# ---------------------------------------------------------------------------
# BL-088 — EPUB/DOCX (both zip+XML containers) had no decompressed-size cap
# and no entity-expansion hardening: a small malicious file could exhaust
# memory/CPU (zip bomb) or trigger entity-expansion blowup before any real
# parsing began.
# ---------------------------------------------------------------------------


def _epub_with_documents(documents: Mapping[str, str], *, spine: tuple[str, ...]) -> bytes:
    """Build a minimal EPUB containing the given XHTML content documents."""
    manifest_items = []
    spine_refs = []
    for index, path in enumerate(documents, start=1):
        item_id = f"c{index}"
        href = path.removeprefix("OEBPS/")
        manifest_items.append(
            f'<item id="{item_id}" href="{href}" media-type="application/xhtml+xml"/>'
        )
        if path in spine:
            spine_refs.append(f'<itemref idref="{item_id}"/>')

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        z.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            f"<manifest>{''.join(manifest_items)}</manifest>"
            f"<spine>{''.join(spine_refs)}</spine></package>",
        )
        for path, content in documents.items():
            z.writestr(path, content)
    return buf.getvalue()


def _epub_with_padding(padding_bytes: int) -> bytes:
    """A minimal, valid EPUB plus one unreferenced entry padded to *padding_bytes*.

    The padding is a single repeated byte written with DEFLATE compression, so
    it collapses to a tiny compressed footprint while still declaring its full
    uncompressed size in the zip's central directory — the same shape a real
    zip bomb takes, just scaled down to a size a test can build in milliseconds.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        z.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            '<manifest><item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
            '</manifest><spine><itemref idref="c1"/></spine></package>',
        )
        z.writestr(
            "OEBPS/ch1.xhtml",
            '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Only chapter.</p></body></html>',
        )
        z.writestr("OEBPS/pad.bin", b"A" * padding_bytes, compress_type=zipfile.ZIP_DEFLATED)
    return buf.getvalue()


def test_epub_zip_bomb_rejected(monkeypatch):
    monkeypatch.setenv("DOCRAY_EXTRACT_MAX_UNCOMPRESSED_MB", "1")
    data = _epub_with_padding(2 * 1024 * 1024)  # 2 MiB uncompressed, over the 1 MiB cap
    with pytest.raises(ValueError, match="uncompressed"):
        extract(data, "bomb", ".epub")


def test_epub_within_size_cap_still_extracts(monkeypatch):
    monkeypatch.setenv("DOCRAY_EXTRACT_MAX_UNCOMPRESSED_MB", "1")
    data = _epub_with_padding(1024)  # trivially under the 1 MiB cap
    e = extract(data, "book", ".epub")
    assert "Only chapter." in e.full_text


_ENTITY_BOMB_XML = """<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
]>
<lolz>&lol2;</lolz>"""


def test_epub_xml_parser_forbids_entity_expansion():
    """The OPF parser itself (extraction.ET) must reject DOCTYPE/ENTITY payloads.

    Proves defusedxml is actually wired in, not just that a malformed OPF
    happens to fail some other way.
    """
    with pytest.raises(Exception, match="[Ee]ntit"):
        extraction.ET.fromstring(_ENTITY_BOMB_XML)


def _epub_with_malicious_opf() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        z.writestr("OEBPS/content.opf", _ENTITY_BOMB_XML)
        z.writestr(
            "OEBPS/ch1.xhtml",
            '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Safe fallback.</p></body></html>',
        )
    return buf.getvalue()


def test_epub_malicious_opf_falls_back_instead_of_crashing():
    """A hostile OPF degrades to the alphabetical-xhtml fallback, not a crash/hang."""
    e = extract(_epub_with_malicious_opf(), "book", ".epub")
    assert "Safe fallback." in e.full_text


def test_unsupported_format_raises():
    with pytest.raises(ValueError):
        extract(b"data", "f", ".xyz")


def test_extraction_strips_nul_bytes():
    # Live incident: a PDF's extracted text carried \x00 bytes, which Postgres
    # text/jsonb columns categorically reject (DataError on ingest, 3 failed
    # attempts). The dispatcher must sanitize every string it hands back.
    data = b"Clear\x00 writing\x00 wins."
    e = extract(data, "writing\x00clearly", ".txt")
    assert "\x00" not in e.full_text
    assert "\x00" not in e.title
    assert "Clear writing wins." in e.full_text


def test_opt_in_pdf_math_backend_accepts_verified_latex(monkeypatch):
    def run(cmd, *, input, capture_output, check, timeout):  # noqa: ANN001
        assert cmd == ["marker", "--json"]
        assert input == b"%PDF math"
        payload = {
            "full_text": "Euler identity:\n\\[e^{i\\pi}+1=0\\]",
            "title": "math paper",
            "page_count": 1,
            "extractor": "marker",
            "extractor_version": "1.2.3",
            "metadata": {"source": "fixture"},
        }
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setenv("DOCRAY_PDF_MATH_BACKEND", "external")
    monkeypatch.setenv("DOCRAY_PDF_MATH_BACKEND_CMD", "marker --json")
    monkeypatch.setattr(extraction.subprocess, "run", run)

    e = extraction.extract_pdf(b"%PDF math", "fallback")

    assert e.full_text.startswith("Euler identity")
    assert e.extractor == "marker"
    assert e.extractor_version == "1.2.3"
    assert e.metadata["math_backend_verified"] is True
    assert e.metadata["source"] == "fixture"


def test_opt_in_pdf_math_backend_rejects_unverified_output(monkeypatch):
    def run(cmd, *, input, capture_output, check, timeout):  # noqa: ANN001
        payload = {
            "full_text": "Euler identity was extracted without equation markup.",
            "extractor": "marker",
            "extractor_version": "1.2.3",
        }
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setenv("DOCRAY_PDF_MATH_BACKEND", "external")
    monkeypatch.setenv("DOCRAY_PDF_MATH_BACKEND_CMD", "marker --json")
    monkeypatch.setattr(extraction.subprocess, "run", run)

    with pytest.raises(ValueError, match="LaTeX"):
        extraction.extract_pdf(b"%PDF math", "fallback")
