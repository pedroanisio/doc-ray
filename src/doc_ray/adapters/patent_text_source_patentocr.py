"""Adapter: :mod:`patent_ocr` behind :class:`PatentTextSource`.

The library is an optional runtime dependency. It is imported inside
:meth:`PatentOcrTextSource.read` rather than at module scope so that importing
``doc_ray`` on an image without it (or without Tesseract and Ghostscript) stays
free — the same lazy-import shape the math backend uses.

Everything this module does is translation. The reading decisions — where the
gutter is, which bands are two-column, what confidence is poor — belong to the
library and are deliberately not second-guessed here.
"""

from __future__ import annotations

import os
import tempfile
from typing import Protocol, cast

from doc_ray.ports.patent_text_source import (
    QUALITY_EMPTY,
    QUALITY_INCOMPLETE,
    QUALITY_LOW_CONFIDENCE,
    QUALITY_MIXED_LAYOUT,
    QUALITY_NO_TEXT_PAGES,
    QUALITY_OK,
    QUALITY_SPARSE_TEXT,
    PatentRead,
    PatentSpan,
    PatentTextUnavailableError,
)


class _LibSpan(Protocol):
    """The shape of ``patent_ocr.spans.Span`` this adapter relies on.

    Stated structurally rather than imported: the library is optional, so its
    types must not be needed to import or type-check ``doc_ray``. It also pins
    exactly which attributes are load-bearing, so a change upstream fails here
    rather than somewhere downstream holding a wrong offset.
    """

    start: int
    end: int
    page: int
    bbox: tuple[float, ...]
    kind: str
    conf: float | None

    def slice(self, text: str) -> str: ...


class _LibSpannedText(Protocol):
    """The shape of ``patent_ocr.spans.SpannedText`` this adapter relies on."""

    text: str
    spans: list[_LibSpan]
    n_pages: int
    units: str
    granularity: str
    caveats: tuple[str, ...]

    @property
    def words(self) -> int: ...

    @property
    def mean_conf(self) -> float | None: ...

    @property
    def low_conf_spans(self) -> int: ...

    def page_quality(self) -> list[dict[str, object]]: ...

    def as_dict(self, include_text: bool = ...) -> dict[str, object]: ...


class _LibDiagnosis(Protocol):
    flags: list[str]


class _LibPolicy(Protocol):
    """``patent_ocr.languages.LanguagePolicy``: jurisdiction -> language spec."""

    def is_ambiguous(self, jurisdiction: str | None) -> bool: ...

    def candidates(self, jurisdiction: str | None) -> list[str]: ...

    def resolve(self, jurisdiction: str | None) -> str: ...


class _LibProbeResult(Protocol):
    chosen: str
    scores: dict[str, float]


class _LibModule(Protocol):
    """The entry points this adapter calls."""

    LanguagePolicy: type[_LibPolicy]

    def read_columns(
        self,
        pdf: str,
        *,
        language: str = ...,
        skip_pages: list[int] | None = ...,
        dpi: int = ...,
        granularity: str = ...,
    ) -> _LibSpannedText: ...

    def probe(self, pdf: str, candidates: list[str], *, dpi: int = ...) -> _LibProbeResult: ...

    def diagnose(self, record: dict[str, object]) -> _LibDiagnosis: ...


#: ``Region.kind`` values the library assigns: left column, right column, and
#: full page width. Mapped to the port's 0-based reading order, with full-width
#: deliberately ``None`` rather than 0 — a full-width band is not "the first of
#: two columns", and flattening the two would invent a column that never existed.
_COLUMN_OF_KIND: dict[str, int | None] = {"L": 0, "R": 1, "F": None}

#: The library's triage flags, in its own decreasing order of explanatory power,
#: mapped onto the port's vocabulary. Unmapped flags fall through to the raw
#: label rather than being dropped: an unrecognised warning is still a warning.
_QUALITY_OF_FLAG = {
    "empty": QUALITY_EMPTY,
    "low-confidence": QUALITY_LOW_CONFIDENCE,
    "weak-confidence": QUALITY_LOW_CONFIDENCE,
    "no-text-pages": QUALITY_NO_TEXT_PAGES,
    "sparse-text": QUALITY_SPARSE_TEXT,
    "mixed-layout": QUALITY_MIXED_LAYOUT,
    "incomplete": QUALITY_INCOMPLETE,
}


class PatentOcrTextSource:
    """Read patent PDFs through ``patent_ocr.read_columns``."""

    def __init__(self, *, dpi: int = 300, granularity: str = "line") -> None:
        """Configure the read.

        Args:
            dpi: Raster resolution handed to the library's column analysis.
            granularity: Span unit — ``"line"`` (default) or ``"word"``. Not
                ``"region"``: one span per column block is too coarse to cite,
                which is the property this whole path exists to preserve.
        """
        if granularity not in ("line", "word"):
            raise ValueError(f"granularity must be line|word, got {granularity!r}")
        self._dpi = dpi
        self._granularity = granularity

    def read(
        self,
        pdf_bytes: bytes,
        *,
        language: str | None = None,
        jurisdiction: str | None = None,
        skip_pages: list[int] | None = None,
    ) -> PatentRead:
        """Return the column-correct text of ``pdf_bytes``."""
        patent_ocr = _import_patent_ocr()
        # read_columns takes a path; the bytes arrive from object storage with no
        # path of their own. NamedTemporaryFile honours TMPDIR, which is what the
        # runtime image points at a writable scratch location for the non-root user.
        with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
            handle.write(pdf_bytes)
            handle.flush()
            os.fsync(handle.fileno())
            lang, how, scores = _choose_language(
                patent_ocr, handle.name, language, jurisdiction, self._dpi
            )
            try:
                spanned = patent_ocr.read_columns(
                    handle.name,
                    language=lang,
                    skip_pages=skip_pages,
                    dpi=self._dpi,
                    granularity=self._granularity,
                )
            except FileNotFoundError as exc:
                # Tesseract or Ghostscript missing from the image. Named
                # explicitly: the alternative is a read that returns nothing and
                # is indistinguishable from a document with no text.
                raise PatentTextUnavailableError(
                    f"the patent OCR engine could not run (language={lang!r}): {exc}. "
                    "Are Tesseract, its language packs, and Ghostscript installed?"
                ) from exc
        read = _to_port(spanned, patent_ocr, lang, self._dpi)
        # How the language was arrived at is part of judging the read: a probed
        # choice carries its scores, a defaulted one says so. Recorded because a
        # wrong model fabricates rather than failing, and the score spread is the
        # only evidence available after the fact.
        read.diagnostics["language_selection"] = how
        read.diagnostics["jurisdiction"] = jurisdiction
        if scores:
            read.diagnostics["language_scores"] = scores
        if skip_pages:
            read.diagnostics["skipped_pages"] = list(skip_pages)
        return read


def _choose_language(
    patent_ocr: _LibModule,
    path: str,
    language: str | None,
    jurisdiction: str | None,
    dpi: int,
) -> tuple[str, str, dict[str, float] | None]:
    """Resolve the Tesseract spec, returning it with how it was chosen.

    Order: an explicit spec wins; otherwise the office decides, and an office
    that publishes in several languages is probed rather than guessed. Only with
    neither signal does this fall back to English — and it says so, because for a
    Cyrillic or CJK scan that fallback is the difference between text and
    confident nonsense.
    """
    explicit = (language or "").strip()
    if explicit:
        return explicit, "explicit", None

    office = (jurisdiction or "").strip().upper()
    if not office:
        return "eng", "fallback", None

    policy = patent_ocr.LanguagePolicy()
    if policy.is_ambiguous(office):
        candidates = policy.candidates(office)
        try:
            result = patent_ocr.probe(path, candidates, dpi=dpi)
        except Exception:  # noqa: BLE001 — probing is an optimisation, not a gate
            # A failed probe must not fail the read: the policy's deterministic
            # first candidate is still a defensible choice, and reporting how the
            # language was picked keeps the weaker basis visible.
            return policy.resolve(office), "policy_after_probe_failed", None
        return result.chosen, "probed", dict(result.scores)

    return policy.resolve(office), "policy", None


def _import_patent_ocr() -> _LibModule:
    """Import the library, or explain precisely what is missing."""
    try:
        import patent_ocr
    except ImportError as exc:
        raise PatentTextUnavailableError(
            "the patent extractor was selected but `patent_ocr` is not installed in this image"
        ) from exc
    # The module has no stubs, so this is the one place the structural contract
    # above is asserted rather than checked. Every use downstream is typed.
    return cast("_LibModule", patent_ocr)


def _to_port(
    spanned: _LibSpannedText, patent_ocr: _LibModule, language: str, dpi: int
) -> PatentRead:
    """Translate a ``SpannedText`` into the port's shape."""
    text = spanned.text or ""
    spans = [
        PatentSpan(
            page=int(span.page),
            column=_COLUMN_OF_KIND.get(span.kind),
            text=span.slice(text),
            bbox=tuple(float(v) for v in span.bbox),  # type: ignore[arg-type]
            char_start=int(span.start),
            char_end=int(span.end),
            confidence=None if span.conf is None else float(span.conf),
        )
        for span in spanned.spans
    ]
    quality, flags = _diagnose(spanned, patent_ocr)
    return PatentRead(
        full_text=text,
        page_count=int(spanned.n_pages or 0),
        engine="patent-ocr",
        engine_version=_version_of(patent_ocr),
        language=language,
        quality=quality,
        spans=spans,
        diagnostics={
            "dpi": dpi,
            "units": spanned.units,
            "granularity": spanned.granularity,
            "words": spanned.words,
            "mean_confidence": spanned.mean_conf,
            "low_confidence_spans": spanned.low_conf_spans,
            "triage_flags": flags,
            # The library states these about its own output; carrying them keeps
            # the caveat attached to the data rather than to its documentation.
            "caveats": list(spanned.caveats),
        },
    )


def _diagnose(spanned: _LibSpannedText, patent_ocr: _LibModule) -> tuple[str, list[str]]:
    """Return the dominant quality label and every raised flag."""
    record = spanned.as_dict(include_text=False)
    record["page_quality"] = spanned.page_quality()
    diagnosis = patent_ocr.diagnose(record)
    flags = list(diagnosis.flags)
    if not flags:
        return QUALITY_OK, flags
    return _QUALITY_OF_FLAG.get(flags[0], flags[0]), flags


def _version_of(patent_ocr: _LibModule) -> str:
    """Report the installed library version for provenance."""
    version = getattr(patent_ocr, "__version__", None)
    if version:
        return str(version)
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as pkg_version

    try:
        return pkg_version("patent-ocr")
    except PackageNotFoundError:
        return "unknown"
