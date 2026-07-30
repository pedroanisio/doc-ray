"""Text/metadata extraction per document type: the dispatcher and registry.

Format families live in sibling modules (D-015): :mod:`.extraction_pdf`
(PyMuPDF layout-aware Markdown + pypdf fallback + opt-in ML math backend),
:mod:`.extraction_markup` (txt/HTML/EPUB), :mod:`.extraction_svg`, and
:mod:`.extraction_office` (DOCX/PPTX), all built on
:mod:`.extraction_common`. This module keeps the extension registry, the
:func:`extract` dispatcher, and the NUL sanitizer — plus re-exports of every
extractor so ``doc_ray.adapters.extraction`` remains the stable import
surface for callers and tests.
"""

from __future__ import annotations

import subprocess as subprocess  # noqa: PLC0414 — tests patch extraction.subprocess.run
from dataclasses import replace
from typing import Any

from defusedxml import (
    ElementTree as ET,  # noqa: N814, F401 — re-export; tests harden via extraction.ET
)

from doc_ray.adapters.extraction_common import Extracted as Extracted
from doc_ray.adapters.extraction_markup import (
    extract_epub as extract_epub,
)
from doc_ray.adapters.extraction_markup import (
    extract_html as extract_html,
)
from doc_ray.adapters.extraction_markup import (
    extract_txt as extract_txt,
)
from doc_ray.adapters.extraction_office import (
    extract_docx as extract_docx,
)
from doc_ray.adapters.extraction_office import (
    extract_pptx as extract_pptx,
)
from doc_ray.adapters.extraction_pdf import (
    OcrUnavailableError as OcrUnavailableError,
)
from doc_ray.adapters.extraction_pdf import (
    extract_pdf as extract_pdf,
)
from doc_ray.adapters.extraction_pdf import (
    extract_pdf_math_backend as extract_pdf_math_backend,
)
from doc_ray.adapters.extraction_pdf import (
    extract_pdf_pymupdf as extract_pdf_pymupdf,
)
from doc_ray.adapters.extraction_pdf import (
    extract_pdf_pypdf as extract_pdf_pypdf,
)
from doc_ray.adapters.extraction_pdf import (
    pdf_heading_thresholds_from_env as pdf_heading_thresholds_from_env,
)
from doc_ray.adapters.extraction_pdf import (
    pdf_math_backend_from_env as pdf_math_backend_from_env,
)
from doc_ray.adapters.extraction_pdf import (
    pdf_ocr_enabled_from_env as pdf_ocr_enabled_from_env,
)
from doc_ray.adapters.extraction_pdf import (
    pdf_ocr_language_from_env as pdf_ocr_language_from_env,
)
from doc_ray.adapters.extraction_svg import (
    extract_svg as extract_svg,
)
from doc_ray.domain.document_formats import content_type_for
from doc_ray.ports.patent_text_source import (
    PatentTextSource,
)
from doc_ray.ports.patent_text_source import (
    PatentTextUnavailableError as PatentTextUnavailableError,
)

# Dispatch table keyed by lowercase file extension. Any of these formats ingests.
_EXTRACTORS = {
    ".pdf": extract_pdf,
    ".txt": extract_txt,
    ".text": extract_txt,
    ".md": extract_txt,
    ".markdown": extract_txt,
    ".html": extract_html,
    ".htm": extract_html,
    ".epub": extract_epub,
    ".svg": extract_svg,
    ".pptx": extract_pptx,
    ".docx": extract_docx,
}


def supported_extensions() -> frozenset[str]:
    """Return every registered extension (lowercase, with the leading dot)."""
    return frozenset(_EXTRACTORS)


def is_supported_extension(extension: str) -> bool:
    """Return whether ``extension`` (with leading dot) has a registered extractor.

    The same allowlist :func:`extract` dispatches on — exposed so callers (e.g.
    ``ingestDocument``, BL-106) can reject an unsupported upload before ever
    persisting its blob, not just before extraction runs.
    """
    return extension.lower() in _EXTRACTORS


def content_type_for_extension(extension: str) -> str:
    """Return the persisted source Content-Type for a supported extension."""
    return content_type_for(extension)


def extract(
    data: bytes,
    filename: str,
    extension: str,
    *,
    extractor: str | None = None,
    patent_source: PatentTextSource | None = None,
    jurisdiction: str | None = None,
    ocr_language: str | None = None,
) -> Extracted:
    """Dispatch to the extractor registered for ``extension``.

    Args:
        data: Raw source bytes.
        filename: Original filename, used as the fallback title.
        extension: File extension (with leading dot) selecting the extractor.
        extractor: Optional named override. ``"patent"`` reads the file through
            :mod:`doc_ray.ports.patent_text_source` instead of the ordinary PDF
            path. Selection is a caller decision and is never inferred from
            content: a paper that merely cites a patent number must not be
            silently routed onto a slower, different-shaped extractor.
        patent_source: The port implementation to use for ``extractor="patent"``.
        jurisdiction: Patent office code, used by the patent path to pick an OCR
            language. A Latin model over Cyrillic or CJK fabricates rather than
            failing, so this is a correctness input, not a preference.
        ocr_language: Explicit Tesseract spec, overriding ``jurisdiction``.

    Returns:
        The extracted document.

    Raises:
        ValueError: If no extractor is registered for the extension, or the
            named ``extractor`` is unknown.
        PatentTextUnavailableError: If the patent path was selected and its
            engine is absent. Never degraded to the ordinary PDF extractor,
            which would emit column-interleaved text that reads as a success.
    """
    if extractor is not None and extractor != _PATENT_EXTRACTOR:
        raise ValueError(f"unknown extractor: {extractor!r}")
    if extractor == _PATENT_EXTRACTOR:
        return _sanitize(_extract_patent(data, filename, patent_source, jurisdiction, ocr_language))
    fn = _EXTRACTORS.get(extension.lower())
    if fn is None:
        raise ValueError(f"unsupported document type: {extension!r}")
    return _sanitize(fn(data, fallback_title=filename))


#: Name callers pass to ``extract(extractor=...)`` for the patent path.
_PATENT_EXTRACTOR = "patent"


def _extract_patent(
    data: bytes,
    filename: str,
    source: PatentTextSource | None,
    jurisdiction: str | None = None,
    ocr_language: str | None = None,
) -> Extracted:
    """Read ``data`` through the patent port and normalize to :class:`Extracted`."""
    if source is None:
        raise PatentTextUnavailableError(
            "the patent extractor was selected but no patent text source is "
            "configured; install the patent OCR engine or ingest without the flag"
        )
    read = source.read(data, language=ocr_language, jurisdiction=jurisdiction)
    metadata: dict[str, Any] = {
        "patent_read_quality": read.quality,
        "patent_read_language": read.language,
    }
    if read.diagnostics:
        metadata["patent_read_diagnostics"] = dict(read.diagnostics)
    return Extracted(
        doc_type="pdf",
        title=filename,
        author=None,
        page_count=read.page_count,
        full_text=read.full_text,
        content_type=content_type_for(".pdf"),
        extractor=read.engine,
        extractor_version=read.engine_version,
        metadata=metadata,
        # Columns are carried through rather than re-derived: only the source
        # knows how it split the page, and a citation that loses the column has
        # lost the thing that made the read correct.
        page_runs=[
            {
                "page": span.page,
                "column": span.column,
                "bbox": span.bbox,
                "text": span.text,
                "char_start": span.char_start,
                "char_end": span.char_end,
                "confidence": span.confidence,
            }
            for span in read.spans
        ]
        or None,
    )


def _sanitize(extracted: Extracted) -> Extracted:
    """Strip NUL (0x00) bytes from every extracted string.

    Real-world PDFs emit NULs in extracted text (embedded glyph/binary
    artifacts), and PostgreSQL ``text``/``jsonb`` values categorically reject
    them — one NUL fails the whole ingest. Sanitizing at the dispatcher covers
    every format with one chokepoint.
    """

    def clean(value: str) -> str:
        return value.replace("\x00", "") if "\x00" in value else value

    return replace(
        extracted,
        title=clean(extracted.title),
        author=clean(extracted.author) if extracted.author else extracted.author,
        full_text=clean(extracted.full_text),
        metadata={k: clean(v) if isinstance(v, str) else v for k, v in extracted.metadata.items()},
    )
