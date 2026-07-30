"""The patent-ocr adapter: translation only, and honest failure.

The library owns every reading decision. What is tested here is that its output
survives the crossing intact — column identity, citation offsets, confidence and
triage — and that a missing engine is named rather than silently yielding an
empty read.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass

import pytest

from doc_ray.adapters import patent_text_source_patentocr as adapter
from doc_ray.ports.patent_text_source import (
    QUALITY_LOW_CONFIDENCE,
    QUALITY_OK,
    PatentTextSource,
    PatentTextUnavailableError,
)

_TEXT = "PRE-COMPRESSED AIR WEAPON\nThe present invention concerns a weapon.\nSheet 1 of 4\n"


@dataclass(frozen=True)
class _Span:
    """Mirrors patent_ocr.spans.Span closely enough to exercise the translation."""

    start: int
    end: int
    page: int
    bbox: tuple[float, float, float, float]
    kind: str
    conf: float | None = None

    def slice(self, text: str) -> str:
        return text[self.start : self.end]


class _Spanned:
    """Mirrors patent_ocr.spans.SpannedText."""

    def __init__(self, spans: list[_Span], *, mean_conf: float | None = 91.0) -> None:
        self.text = _TEXT
        self.spans = spans
        self.n_pages = 9
        self.units = "px"
        self.dpi = 300
        self.granularity = "line"
        self.caveats = ("Verify against the scan before citing.",)
        self.words = len(_TEXT.split())
        self.mean_conf = mean_conf
        self.low_conf_spans = sum(1 for s in spans if s.conf is not None and s.conf < 70)

    def page_quality(self) -> list[dict]:
        return [{"page": 0, "spans": len(self.spans), "words": self.words}]

    def as_dict(self, include_text: bool = True) -> dict:
        return {"id": "US-4336787", "n_pages": self.n_pages, "words": self.words}


class _Diagnosis:
    def __init__(self, flags: list[str]) -> None:
        self.flags = flags


def _fake_library(spanned: _Spanned, *, flags: list[str] | None = None) -> types.ModuleType:
    """A stand-in patent_ocr module, so tests need no Tesseract."""
    module = types.ModuleType("patent_ocr")
    module.__version__ = "0.1.0"  # type: ignore[attr-defined]
    calls: list[dict] = []

    def read_columns(path: str, **kwargs: object) -> _Spanned:
        calls.append({"path": path, **kwargs})
        return spanned

    module.read_columns = read_columns  # type: ignore[attr-defined]
    module.diagnose = lambda record: _Diagnosis(list(flags or []))  # type: ignore[attr-defined]
    module.calls = calls  # type: ignore[attr-defined]
    return module


@pytest.fixture
def installed(monkeypatch: pytest.MonkeyPatch):
    """Install a fake patent_ocr for the duration of one test."""

    def _install(spanned: _Spanned, *, flags: list[str] | None = None) -> types.ModuleType:
        module = _fake_library(spanned, flags=flags)
        monkeypatch.setitem(sys.modules, "patent_ocr", module)
        return module

    return _install


_TWO_COLUMN_SPANS = [
    _Span(0, 25, 0, (0.0, 0.0, 100.0, 20.0), "L", conf=95.0),
    _Span(26, 65, 0, (110.0, 0.0, 210.0, 20.0), "R", conf=88.0),
    _Span(66, 78, 1, (0.0, 0.0, 210.0, 20.0), "F", conf=71.0),
]


def test_the_adapter_satisfies_the_port() -> None:
    assert isinstance(adapter.PatentOcrTextSource(), PatentTextSource)


def test_column_identity_survives_translation(installed) -> None:
    """L/R/F is the library's claim about the page; flattening it loses the read."""
    installed(_Spanned(_TWO_COLUMN_SPANS))
    read = adapter.PatentOcrTextSource().read(b"%PDF-1.4\n")
    assert [span.column for span in read.spans] == [0, 1, None]


def test_a_full_width_band_is_not_reported_as_the_first_column(installed) -> None:
    """`F` means the source read the whole width — not "left of two"."""
    installed(_Spanned([_Span(66, 78, 1, (0.0, 0.0, 210.0, 20.0), "F")]))
    read = adapter.PatentOcrTextSource().read(b"%PDF-1.4\n")
    assert read.spans[0].column is None


def test_citation_offsets_address_the_returned_text(installed) -> None:
    """The offsets are the product; a span that misaddresses its own text is worse
    than no span, because it cites confidently and wrongly."""
    installed(_Spanned(_TWO_COLUMN_SPANS))
    read = adapter.PatentOcrTextSource().read(b"%PDF-1.4\n")
    for span in read.spans:
        assert read.full_text[span.char_start : span.char_end] == span.text


def test_confidence_is_carried_not_discarded(installed) -> None:
    installed(_Spanned(_TWO_COLUMN_SPANS))
    read = adapter.PatentOcrTextSource().read(b"%PDF-1.4\n")
    assert [span.confidence for span in read.spans] == [95.0, 88.0, 71.0]


def test_a_clean_read_reports_ok(installed) -> None:
    installed(_Spanned(_TWO_COLUMN_SPANS), flags=[])
    assert adapter.PatentOcrTextSource().read(b"%PDF-1.4\n").quality == QUALITY_OK


def test_the_dominant_triage_flag_becomes_the_quality(installed) -> None:
    installed(_Spanned(_TWO_COLUMN_SPANS, mean_conf=42.0), flags=["low-confidence", "sparse-text"])
    read = adapter.PatentOcrTextSource().read(b"%PDF-1.4\n")
    assert read.quality == QUALITY_LOW_CONFIDENCE
    assert read.diagnostics["triage_flags"] == ["low-confidence", "sparse-text"]


def test_an_unrecognised_flag_is_surfaced_rather_than_dropped(installed) -> None:
    """An unmapped warning is still a warning; silently discarding it would report
    a clean read for a document the library flagged."""
    installed(_Spanned(_TWO_COLUMN_SPANS), flags=["some-future-flag"])
    assert adapter.PatentOcrTextSource().read(b"%PDF-1.4\n").quality == "some-future-flag"


def test_the_library_caveats_ride_with_the_data(installed) -> None:
    """The caveats are about this output; leaving them in the docs detaches them."""
    installed(_Spanned(_TWO_COLUMN_SPANS))
    read = adapter.PatentOcrTextSource().read(b"%PDF-1.4\n")
    assert read.diagnostics["caveats"] == ["Verify against the scan before citing."]


def test_the_requested_language_reaches_the_library(installed) -> None:
    module = installed(_Spanned(_TWO_COLUMN_SPANS))
    adapter.PatentOcrTextSource().read(b"%PDF-1.4\n", language="deu+frk")
    assert module.calls[0]["language"] == "deu+frk"


def test_an_uninstalled_library_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing dependency must not read as a document with no text."""
    monkeypatch.setitem(sys.modules, "patent_ocr", None)
    with pytest.raises(PatentTextUnavailableError, match="not installed"):
        adapter.PatentOcrTextSource().read(b"%PDF-1.4\n")


def test_a_missing_engine_binary_is_named(installed) -> None:
    """Tesseract/Ghostscript absent is a deployment fault, not a document fault."""
    module = installed(_Spanned(_TWO_COLUMN_SPANS))

    def explode(path: str, **kwargs: object) -> _Spanned:
        raise FileNotFoundError("tesseract")

    module.read_columns = explode  # type: ignore[attr-defined]
    with pytest.raises(PatentTextUnavailableError, match="Tesseract"):
        adapter.PatentOcrTextSource().read(b"%PDF-1.4\n")


def test_region_granularity_is_refused() -> None:
    """One span per column block cannot be cited, which defeats the port."""
    with pytest.raises(ValueError, match="granularity"):
        adapter.PatentOcrTextSource(granularity="region")
