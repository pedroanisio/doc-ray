"""Document sources: the shared result dataclass and the source registry.

Mirror of :mod:`doc_ray.adapters.extraction`'s dispatcher role, but for
*origins* instead of formats: ``resolve_source`` picks the adapter whose
``matches`` accepts an operator-supplied reference. Local filesystem paths
match no source — callers treat a ``None`` resolution as "ingest as a path".

The registry is a public tuple on purpose (the extraction registry's private
``_EXTRACTORS`` forced the CLI into an underscore import — not repeated here).
"""

from __future__ import annotations

from dataclasses import dataclass

from doc_ray.adapters.document_source_arxiv import ArxivSource
from doc_ray.ports.document_source import DocumentSource


@dataclass
class Fetched:
    """Concrete ``FetchedDocument``: bytes + intake metadata from one source."""

    data: bytes
    filename: str
    source_url: str
    title: str | None = None


#: Every available source, in match order. Adding an origin = one adapter
#: module implementing ``DocumentSource`` + one entry here.
SOURCES: tuple[DocumentSource, ...] = (ArxivSource(),)


def resolve_source(ref: str) -> DocumentSource | None:
    """Return the first source that claims *ref*, or ``None`` for local paths."""
    for source in SOURCES:
        if source.matches(ref):
            return source
    return None
