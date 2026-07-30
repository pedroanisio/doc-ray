"""The patent path from upload form to extractor.

The property under test is that the *caller's* choice survives every hop, and
that language — which decides whether the OCR reads or fabricates — is resolved
from the office rather than defaulted to English on the way through.
"""

from __future__ import annotations

import pytest

from doc_ray.adapters.patent_text_source_factory import (
    build_patent_source,
    patent_dpi_from_env,
    patent_extraction_enabled,
)
from doc_ray.application.pipeline import jurisdiction_from_filename


class TestEnablement:
    """The path is opt-in: it costs a rasterisation pass per page."""

    def test_it_is_off_by_default(self) -> None:
        assert patent_extraction_enabled({}) is False

    @pytest.mark.parametrize("raw", ["1", "true", "yes", "on", "ON", " True "])
    def test_recognised_truthy_values(self, raw: str) -> None:
        assert patent_extraction_enabled({"DOCRAY_PATENT_EXTRACTION": raw}) is True

    @pytest.mark.parametrize("raw", ["0", "false", "no", "off", "", "maybe"])
    def test_everything_else_is_off(self, raw: str) -> None:
        assert patent_extraction_enabled({"DOCRAY_PATENT_EXTRACTION": raw}) is False

    def test_no_source_is_built_when_disabled(self) -> None:
        """Disabled must yield None, not a source that fails deep in a worker."""
        assert build_patent_source({}) is None


class TestDpi:
    """300 is what the recall figures were measured at; drift is not silent."""

    def test_default(self) -> None:
        assert patent_dpi_from_env({}) == 300

    def test_explicit(self) -> None:
        assert patent_dpi_from_env({"DOCRAY_PATENT_OCR_DPI": "150"}) == 150

    @pytest.mark.parametrize("raw", ["", "abc", "0", "-1"])
    def test_unusable_values_fall_back_rather_than_crash_an_ingest(self, raw: str) -> None:
        assert patent_dpi_from_env({"DOCRAY_PATENT_OCR_DPI": raw}) == 300


class TestJurisdictionFromFilename:
    """Patent collections are named ``XX-1234567``; reading that is free accuracy."""

    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("US-4336787.pdf", "US"),
            ("DE-0154179-B.pdf", "DE"),
            ("SU1234567.pdf", "SU"),
            ("JP 0050123 A.pdf", "JP"),
            ("fr-0918278.pdf", "FR"),
        ],
    )
    def test_office_codes_are_read(self, filename: str, expected: str) -> None:
        assert jurisdiction_from_filename(filename) == expected

    @pytest.mark.parametrize(
        "filename",
        [
            "An-analysis-of-airguns.pdf",
            "notes.pdf",
            "Chapter 3.pdf",
            "US patent overview.pdf",
        ],
    )
    def test_ordinary_names_resolve_to_nothing(self, filename: str) -> None:
        """A wrong guess here picks a wrong OCR model, so the pattern is strict:
        two letters alone are not an office code without a number behind them."""
        assert jurisdiction_from_filename(filename) is None


class _RecordingSource:
    """Captures what the extractor was asked for."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def read(
        self,
        pdf_bytes: bytes,
        *,
        language: str | None = None,
        jurisdiction: str | None = None,
        skip_pages: list[int] | None = None,
    ):
        from doc_ray.ports.patent_text_source import PatentRead

        self.calls.append({"language": language, "jurisdiction": jurisdiction})
        return PatentRead(
            full_text="text",
            page_count=1,
            engine="patent-ocr",
            engine_version="0.1.0",
            language=language or "eng",
        )


def test_the_office_reaches_the_source() -> None:
    """A Cyrillic scan read with an English model fabricates, so this is the
    difference between text and confident noise."""
    from doc_ray.adapters import extraction

    source = _RecordingSource()
    extraction.extract(
        b"%PDF-1.4\n",
        "SU-1234567.pdf",
        ".pdf",
        extractor="patent",
        patent_source=source,
        jurisdiction="SU",
    )
    assert source.calls[0]["jurisdiction"] == "SU"


def test_an_explicit_language_reaches_the_source() -> None:
    from doc_ray.adapters import extraction

    source = _RecordingSource()
    extraction.extract(
        b"%PDF-1.4\n",
        "DE-0154179.pdf",
        ".pdf",
        extractor="patent",
        patent_source=source,
        ocr_language="deu+frk",
    )
    assert source.calls[0]["language"] == "deu+frk"
