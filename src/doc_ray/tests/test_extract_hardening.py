"""Extraction hardening + opt-in backends: BL-088 zip/XML guards, PDF math backend.

Split out of test_extract_formats.py (D-015): the EPUB/DOCX decompression-bomb
and XML entity-expansion guards (BL-088), unsupported-format and NUL-byte
handling, and the opt-in external PDF math backend contract.
"""

from __future__ import annotations

import io
import json
import subprocess
import zipfile

import pytest

from doc_ray.adapters import extraction
from doc_ray.adapters.extraction import extract

# ---------------------------------------------------------------------------
# BL-088 — EPUB/DOCX (both zip+XML containers) had no decompressed-size cap
# and no entity-expansion hardening: a small malicious file could exhaust
# memory/CPU (zip bomb) or trigger entity-expansion blowup before any real
# parsing began.
# ---------------------------------------------------------------------------


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
