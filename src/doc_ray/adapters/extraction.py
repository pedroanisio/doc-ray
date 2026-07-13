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

_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".text": "text/plain",
    ".md": "text/plain",
    ".markdown": "text/plain",
    ".html": "text/html",
    ".htm": "text/html",
    ".epub": "application/epub+zip",
    ".svg": "image/svg+xml",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def is_supported_extension(extension: str) -> bool:
    """Return whether ``extension`` (with leading dot) has a registered extractor.

    The same allowlist :func:`extract` dispatches on — exposed so callers (e.g.
    ``ingestDocument``, BL-106) can reject an unsupported upload before ever
    persisting its blob, not just before extraction runs.
    """
    return extension.lower() in _EXTRACTORS


def content_type_for_extension(extension: str) -> str:
    """Return the persisted source Content-Type for a supported extension."""
    try:
        return _CONTENT_TYPES[extension.lower()]
    except KeyError as exc:
        raise ValueError(f"unsupported document type: {extension!r}") from exc


def extract(data: bytes, filename: str, extension: str) -> Extracted:
    """Dispatch to the extractor registered for ``extension``.

    Args:
        data: Raw source bytes.
        filename: Original filename, used as the fallback title.
        extension: File extension (with leading dot) selecting the extractor.

    Returns:
        The extracted document.

    Raises:
        ValueError: If no extractor is registered for the extension.
    """
    fn = _EXTRACTORS.get(extension.lower())
    if fn is None:
        raise ValueError(f"unsupported document type: {extension!r}")
    return _sanitize(fn(data, fallback_title=filename))


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
