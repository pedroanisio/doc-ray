"""Port: fetch document bytes from a remote reference (URL, id scheme).

A ``DocumentSource`` turns an operator-supplied reference — an ``https://``
URL, an ``arxiv:<id>`` ref — into the bytes + filename the ingest core needs.
Sources are the intake mirror of ``TextExtractor``: extractors make formats
pluggable, sources make *origins* pluggable. The application core never
performs network I/O itself; it asks the resolved source to fetch and hands
the result to ``pipeline.ingest_bytes``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class FetchedDocument(Protocol):
    """The shape a source returns (see ``adapters.document_source.Fetched``).

    Attributes:
        data: Raw document bytes as downloaded.
        filename: Safe basename (with extension) that drives extractor
            dispatch and the default title — never a caller-controlled path.
        source_url: Canonical URL the bytes were fetched from (recorded in the
            document's metadata for provenance).
        title: Authoritative title from the source's own metadata (e.g. the
            arXiv Atom API), when one is available; ``None`` otherwise.
    """

    data: bytes
    filename: str
    source_url: str
    title: str | None


class DocumentSource(Protocol):
    """Fetch a document from one class of remote references.

    Attributes:
        name: Short registry/display name (e.g. ``"arxiv"``).
    """

    name: str

    def matches(self, ref: str) -> bool:
        """Return whether *ref* belongs to this source (cheap, no network)."""
        ...

    def fetch(self, ref: str) -> FetchedDocument:
        """Download *ref* and return its bytes + intake metadata.

        Args:
            ref: A reference for which :meth:`matches` returned ``True``.

        Raises:
            ValueError: If *ref* does not belong to this source or the
                response is not the promised document type.
            httpx.HTTPError: On transport or HTTP-status failures.
        """
        ...
