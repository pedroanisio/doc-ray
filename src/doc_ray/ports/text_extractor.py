"""Port: extract text + metadata from raw document bytes."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ExtractedText(Protocol):
    """The shape an extractor returns (see adapters.extraction.Extracted)."""
    title: str
    full_text: str
    page_count: int | None
    content_type: str
    extractor: str
    extractor_version: str


class TextExtractor(Protocol):
    """Turn document bytes into text + metadata.

    Fulfilled by ``doc_ray.adapters.extraction.extract`` (dispatch by extension:
    pdf via PyMuPDF with a pypdf fallback; txt/md/html/epub/docx via stdlib).
    """
    def __call__(self, data: bytes, filename: str, extension: str) -> ExtractedText: ...
