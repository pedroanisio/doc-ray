"""The one definition of which document formats ingest.

The extension allowlist used to live only inside
:mod:`doc_ray.adapters.extraction`, whose import pulls PyMuPDF, pypdf and
python-docx into the process. Surfaces that only need to *name* the formats —
the upload form, the CLI help, the docs — could not afford that import, so each
re-typed the list by hand and each copy drifted: HTML, SVG and PPTX have had
working extractors while the upload form's ``accept`` attribute offered five
extensions and the prose named five formats.

This module is dependency-free on purpose. The extractor registry is asserted
against it in ``tests/test_document_formats.py``, so a new format cannot be
dispatchable and unlisted at the same time.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

#: Extension -> Content-Type stored with the original bytes. Lowercase, dotted.
CONTENT_TYPES: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".text": "text/plain",
        ".md": "text/plain",
        ".markdown": "text/plain",
        ".html": "text/html",
        ".htm": "text/html",
        ".epub": "application/epub+zip",
        ".svg": "image/svg+xml",
        ".pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    }
)

#: Every extension that has a registered extractor.
SUPPORTED_EXTENSIONS: Final[frozenset[str]] = frozenset(CONTENT_TYPES)

#: Value for an ``<input type=file accept=...>`` attribute. The browser hint is
#: only a hint — the server enforces the same allowlist before storing a blob
#: (BL-106) — but a narrower hint hides working formats from the operator.
ACCEPT_ATTRIBUTE: Final[str] = ",".join(sorted(SUPPORTED_EXTENSIONS))

#: Display name per format family, in the order humans expect to read them.
#: Several extensions share one family (``.md``/``.markdown``), so this is not
#: derivable from the extension list alone.
_FAMILIES: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("PDF", (".pdf",)),
    ("EPUB", (".epub",)),
    ("HTML", (".html", ".htm")),
    ("Markdown", (".md", ".markdown")),
    ("plain text", (".txt", ".text")),
    ("DOCX", (".docx",)),
    ("PPTX", (".pptx",)),
    ("SVG", (".svg",)),
)

#: Human-facing format families backed by at least one registered extension.
#: Documentation gates import this tuple so prose cannot maintain a separate
#: hand-written inventory from the executable extension allowlist.
SUPPORTED_FORMAT_NAMES: Final[tuple[str, ...]] = tuple(
    name
    for name, extensions in _FAMILIES
    if any(extension in SUPPORTED_EXTENSIONS for extension in extensions)
)


def format_label(*, conjunction: str = "and") -> str:
    """Return the human-readable format list, derived from the allowlist.

    Args:
        conjunction: Word joining the final item (``"and"``, ``"or"``).

    Returns:
        A prose list such as ``"PDF, EPUB, HTML, Markdown, plain text, DOCX,
        PPTX, and SVG"`` — covering every family whose extensions are
        supported, so the sentence cannot fall behind the registry.
    """
    names = SUPPORTED_FORMAT_NAMES
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])}, {conjunction} {names[-1]}"


def content_type_for(extension: str) -> str:
    """Return the stored Content-Type for *extension* (with leading dot).

    Args:
        extension: File extension, any case, including the leading dot.

    Raises:
        ValueError: The extension has no registered extractor.
    """
    try:
        return CONTENT_TYPES[extension.lower()]
    except KeyError as exc:
        raise ValueError(f"unsupported document type: {extension!r}") from exc
