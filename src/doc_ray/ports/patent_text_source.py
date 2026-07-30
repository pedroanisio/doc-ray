"""Port: read a multi-column patent PDF as ordered, citable text.

Separate from the ordinary PDF path because the two disagree about what a page
*is*. :mod:`doc_ray.adapters.extraction_pdf` orders lines by ``y`` and treats a
page as one column of prose; a patent front page is two columns, so that reading
interleaves them line by line and yields sentences that never existed. The
damage is not cosmetic — it is what the reader, the sentence index and every
citation offset are built on.

This port also inverts the OCR trigger. The ordinary path OCRs only when the
layout extractor found *no* text, which silently exempts the common scanned
patent: one that already carries a poor OCR text layer. Non-empty is not the
same as usable, so a source behind this port re-reads the rasters regardless of
what text the container claims to have.

Selection is explicit (an ingest-time flag), never sniffed from content: a paper
that merely *cites* a patent number must not be silently routed onto a slower,
different-shaped extractor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

#: Triage labels a source may attach to a read. They describe the *confidence of
#: the read*, not the document — ``low_confidence`` means "believe this less",
#: which is a claim the ingest pipeline is allowed to record but never to hide.
QUALITY_OK = "ok"
QUALITY_LOW_CONFIDENCE = "low_confidence"
QUALITY_NO_TEXT_PAGES = "no_text_pages"
QUALITY_SPARSE_TEXT = "sparse_text"
QUALITY_MIXED_LAYOUT = "mixed_layout"
QUALITY_INCOMPLETE = "incomplete"
QUALITY_EMPTY = "empty"


@dataclass(frozen=True)
class PatentSpan:
    """One run of text with the geometry and offsets that make it citable.

    ``char_start``/``char_end`` address :attr:`PatentRead.full_text`, the same
    shape doc-ray's sentence citations already use, so a claim keeps its anchor
    without anyone re-deriving offsets from text that OCR may have re-flowed.

    ``column`` is 0-based *within the page* in reading order — 0 then 1 on a
    two-column page — and ``None`` where the source read the full page width. It
    is carried rather than inferred because only the source knows how it split
    the page, and "full width" is a different claim from "left column".
    """

    page: int  # 0-based, matching page_runs elsewhere in the codebase
    column: int | None
    text: str
    bbox: tuple[float, float, float, float]
    char_start: int
    char_end: int
    #: Engine confidence 0-100 where reported. High confidence can still be
    #: wrong, so this bounds trust rather than establishing it.
    confidence: float | None = None


@dataclass(frozen=True)
class PatentRead:
    """A complete read of one patent PDF.

    ``full_text`` is authoritative for the sentence index; ``spans`` is the
    sibling geometry view (the same relationship ``Extracted.page_runs`` has to
    ``Extracted.full_text``), so callers that only want text never pay for
    geometry and callers that want citations never re-derive it.
    """

    full_text: str
    page_count: int
    engine: str  # tool that produced the text, for provenance
    engine_version: str
    language: str
    quality: str = QUALITY_OK
    spans: list[PatentSpan] = field(default_factory=list)
    #: Free-form per-read diagnostics (confidence means, pages re-OCRed, ...).
    #: Recorded in provenance; never load-bearing for correctness.
    diagnostics: dict[str, object] = field(default_factory=dict)


class PatentTextUnavailableError(RuntimeError):
    """The patent path was requested and could not run.

    Deliberately not a fall-through to the ordinary PDF extractor: that path
    produces column-interleaved text which *looks* like a successful ingest.
    Failing loudly keeps a missing dependency distinguishable from a document
    that genuinely has no text.
    """


@runtime_checkable
class PatentTextSource(Protocol):
    """Read a patent PDF's text with columns separated and rasters re-OCRed."""

    def read(
        self,
        pdf_bytes: bytes,
        *,
        language: str | None = None,
        jurisdiction: str | None = None,
        skip_pages: list[int] | None = None,
    ) -> PatentRead:
        """Return the ordered text of ``pdf_bytes``.

        Language is not a cosmetic setting here. A Latin model run over Cyrillic
        or CJK does not degrade gracefully — it emits confident Latin lookalikes
        at zero percent correct script. Getting it wrong therefore produces
        fabricated text that passes every downstream check, so the source is
        given the means to choose properly rather than defaulting to English.

        Args:
            pdf_bytes: Raw PDF bytes.
            language: Explicit Tesseract spec (e.g. ``"deu+frk"``). Wins over
                ``jurisdiction`` when both are given.
            jurisdiction: Patent office code (``"DE"``, ``"SU"``, ``"JP"``, …).
                The source resolves it to a language, probing when the office
                publishes in several.
            skip_pages: 1-based pages to leave out, for scans carrying
                synthetic placeholder pages.

        Raises:
            PatentTextUnavailableError: The engine or its language data is absent.
        """
        ...
