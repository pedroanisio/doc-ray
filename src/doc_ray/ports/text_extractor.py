"""Port: extract text + metadata from raw document bytes."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ExtractedText(Protocol):
    """The shape an extractor returns (see ``adapters.extraction.Extracted``).

    Attributes:
        title: Human-readable document title.
        full_text: The extracted plain/markdown text body.
        page_count: Number of pages, when the format is paginated.
        content_type: MIME type of the source document.
        extractor: Name of the tool that produced the text.
        extractor_version: Version of that tool (recorded in provenance).
    """

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

    def __call__(self, data: bytes, filename: str, extension: str) -> ExtractedText:
        """Extract text and metadata from *data*.

        Args:
            data: Raw document bytes.
            filename: Original filename (used as a title fallback).
            extension: File extension, including the leading dot.

        Returns:
            An ``ExtractedText`` describing the document's text and metadata.
        """
        ...
