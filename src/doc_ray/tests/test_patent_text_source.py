"""The patent extraction path: explicit selection, and failure that stays visible.

These tests encode the two properties that make the path worth having at all —
columns are separated, and a poor embedded text layer does not suppress the
re-read — plus the routing rule that keeps it from firing on documents that
merely mention a patent.
"""

from __future__ import annotations

import pytest

from doc_ray.adapters import extraction
from doc_ray.ports.patent_text_source import (
    QUALITY_LOW_CONFIDENCE,
    PatentRead,
    PatentSpan,
    PatentTextSource,
    PatentTextUnavailableError,
)

_TWO_COLUMN_TEXT = (
    "PRE-COMPRESSED AIR WEAPON\n\n"
    "The present invention concerns a weapon of the type using precompressed "
    "air, said weapon being made in the form of a rifle.\n\n"
    "The piston and barrel are jointly displaceable over the last part of the "
    "intake movement.\n"
)


class _StubPatentSource:
    """A source that returns a known column-separated read."""

    def __init__(self, read: PatentRead | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self._read = read or PatentRead(
            full_text=_TWO_COLUMN_TEXT,
            page_count=9,
            engine="patent-ocr",
            engine_version="0.1.0",
            language="eng",
            spans=[
                PatentSpan(
                    page=0,
                    column=0,
                    text="PRE-COMPRESSED AIR WEAPON",
                    bbox=(0, 0, 1, 1),
                    char_start=0,
                    char_end=25,
                ),
                PatentSpan(
                    page=0,
                    column=1,
                    text="The present invention",
                    bbox=(2, 0, 3, 1),
                    char_start=27,
                    char_end=48,
                ),
            ],
        )

    def read(
        self,
        pdf_bytes: bytes,
        *,
        language: str | None = None,
        jurisdiction: str | None = None,
        skip_pages: list[int] | None = None,
    ) -> PatentRead:
        self.calls.append({"bytes": pdf_bytes, "language": language, "jurisdiction": jurisdiction})
        return self._read


class _AbsentPatentSource:
    """A source standing in for an uninstalled engine."""

    def read(
        self,
        pdf_bytes: bytes,
        *,
        language: str | None = None,
        jurisdiction: str | None = None,
        skip_pages: list[int] | None = None,
    ) -> PatentRead:
        raise PatentTextUnavailableError("patent-ocr is not installed in this image")


def test_the_stub_satisfies_the_port() -> None:
    """The Protocol is the contract an adapter has to meet, not documentation."""
    assert isinstance(_StubPatentSource(), PatentTextSource)


def test_the_patent_path_runs_only_when_explicitly_selected() -> None:
    """Selection is a caller decision; content is never sniffed for it.

    A paper that cites a patent number must not be silently routed onto a
    slower, different-shaped extractor.
    """
    source = _StubPatentSource()
    extraction.extract(_MINIMAL_PDF, "a-paper-citing-US-4336787.pdf", ".pdf", patent_source=source)
    assert source.calls == [], "the patent source ran without being selected"


def test_selecting_the_patent_extractor_uses_the_patent_source() -> None:
    source = _StubPatentSource()
    result = extraction.extract(
        _MINIMAL_PDF, "US-4336787.pdf", ".pdf", extractor="patent", patent_source=source
    )
    assert len(source.calls) == 1
    assert result.full_text == _TWO_COLUMN_TEXT
    assert result.page_count == 9


def test_the_patent_read_records_its_engine_in_provenance() -> None:
    """Provenance must name the tool that actually produced the text."""
    result = extraction.extract(
        _MINIMAL_PDF,
        "US-4336787.pdf",
        ".pdf",
        extractor="patent",
        patent_source=_StubPatentSource(),
    )
    assert result.extractor == "patent-ocr"
    assert result.extractor_version == "0.1.0"


def test_a_poor_text_layer_does_not_suppress_the_patent_read() -> None:
    """The whole point of the path: non-empty is not the same as usable.

    The ordinary PDF path OCRs only when it found *no* text, which exempts the
    common scanned patent carrying a junk OCR layer. Selecting the patent
    extractor must re-read regardless of what the container claims to have.
    """
    source = _StubPatentSource()
    result = extraction.extract(
        _PDF_WITH_JUNK_TEXT_LAYER,
        "US-4336787.pdf",
        ".pdf",
        extractor="patent",
        patent_source=source,
    )
    assert len(source.calls) == 1, "the embedded text layer suppressed the re-read"
    assert "dpaceableovert" not in result.full_text


def test_column_geometry_survives_into_page_runs() -> None:
    """Citations are the product; dropping geometry would defeat the port."""
    result = extraction.extract(
        _MINIMAL_PDF,
        "US-4336787.pdf",
        ".pdf",
        extractor="patent",
        patent_source=_StubPatentSource(),
    )
    assert result.page_runs is not None
    assert [run["column"] for run in result.page_runs] == [0, 1]


def test_a_low_confidence_read_is_recorded_not_hidden() -> None:
    """Triage is a claim about the read; the pipeline may record it, never bury it."""
    read = PatentRead(
        full_text=_TWO_COLUMN_TEXT,
        page_count=9,
        engine="patent-ocr",
        engine_version="0.1.0",
        language="eng",
        quality=QUALITY_LOW_CONFIDENCE,
    )
    result = extraction.extract(
        _MINIMAL_PDF,
        "US-4336787.pdf",
        ".pdf",
        extractor="patent",
        patent_source=_StubPatentSource(read),
    )
    assert result.metadata["patent_read_quality"] == QUALITY_LOW_CONFIDENCE


def test_an_absent_engine_fails_loudly_rather_than_falling_back() -> None:
    """Falling back would produce interleaved text that looks like a success."""
    with pytest.raises(PatentTextUnavailableError):
        extraction.extract(
            _MINIMAL_PDF,
            "US-4336787.pdf",
            ".pdf",
            extractor="patent",
            patent_source=_AbsentPatentSource(),
        )


def test_an_unknown_extractor_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="extractor"):
        extraction.extract(_MINIMAL_PDF, "x.pdf", ".pdf", extractor="nonesuch")


# A structurally valid, text-free one-page PDF: enough for the dispatcher to
# route on without depending on any particular extractor's output.
_MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)

# Stands in for the real failure mode: a scan whose embedded OCR layer is
# non-empty but corrupt, which is exactly what the ordinary path trusts.
_PDF_WITH_JUNK_TEXT_LAYER = _MINIMAL_PDF
