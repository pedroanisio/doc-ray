"""arXiv document source: id/URL normalization, PDF download, Atom title fetch.

Accepted references (all normalize to a canonical arXiv id):

- ``https://arxiv.org/abs/2607.20638`` (also ``www.`` / ``export.`` hosts)
- ``https://arxiv.org/pdf/2607.20638`` (with or without ``.pdf`` / ``vN``)
- ``arxiv:2607.20638`` / bare ``2607.20638``
- old-style ids: ``math.GT/0309136``

The PDF is fetched from ``https://arxiv.org/pdf/<id>`` and must carry the
``%PDF-`` magic — arXiv serves HTML interstitials with status 200, and those
must fail here, not at extraction. The title comes from the export Atom API;
metadata failure never blocks an ingest (the extractor title remains as
fallback).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import httpx
from defusedxml import ElementTree as SafeET

if TYPE_CHECKING:
    from doc_ray.adapters.document_source import Fetched

_PDF_URL = "https://arxiv.org/pdf/{id}"
_ATOM_URL = "https://export.arxiv.org/api/query"
_ATOM_TITLE = ".//{http://www.w3.org/2005/Atom}entry/{http://www.w3.org/2005/Atom}title"
_HOSTS = frozenset({"arxiv.org", "www.arxiv.org", "export.arxiv.org"})
_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
# arXiv's automated-access policy asks clients to identify themselves.
_HEADERS = {"User-Agent": "doc-ray-ingest/1.0 (+https://github.com/pedroanisio/doc-ray)"}

# New-style (2007+) ids: YYMM.NNNN(N) — and old-style archive/NNNNNNN ids,
# both with an optional vN suffix.
_ID = re.compile(
    r"^(?:(?P<new>\d{4}\.\d{4,5})|(?P<old>[a-zA-Z][a-zA-Z-]*(?:\.[a-zA-Z]{2})?/\d{7}))"
    r"(?P<ver>v\d+)?$"
)


def normalize_arxiv_ref(ref: str) -> str | None:
    """Return the canonical arXiv id for *ref*, or ``None`` if it is not one.

    Args:
        ref: URL, ``arxiv:<id>`` ref, or bare id (see module docstring).

    Returns:
        The id with any ``vN`` suffix preserved (e.g. ``"2607.20638v2"``,
        ``"math.GT/0309136"``), or ``None`` when *ref* is not an arXiv
        reference.
    """
    candidate = ref.strip()
    if not candidate:
        return None
    lowered = candidate.lower()
    if lowered.startswith("arxiv:"):
        candidate = candidate[len("arxiv:") :]
    elif lowered.startswith(("http://", "https://")):
        parts = urlsplit(candidate)
        if parts.hostname not in _HOSTS:
            return None
        path = parts.path.strip("/")
        # Old-style ids contain a slash, so split off only the route segment.
        route, _, rest = path.partition("/")
        if route not in {"abs", "pdf"} or not rest:
            return None
        candidate = rest.removesuffix(".pdf")
    match = _ID.match(candidate)
    if match is None:
        return None
    return match.group(0)


class ArxivSource:
    """``DocumentSource`` adapter for arXiv (see ``ports.document_source``)."""

    name = "arxiv"

    def __init__(self, client: httpx.Client | None = None) -> None:
        """Create the source.

        Args:
            client: Injected HTTP client (tests use ``httpx.MockTransport``).
                When ``None``, each fetch opens and closes its own client.
        """
        self._client = client

    def matches(self, ref: str) -> bool:
        """Return whether *ref* is an arXiv reference (no network)."""
        return normalize_arxiv_ref(ref) is not None

    def fetch(self, ref: str) -> Fetched:
        """Download the PDF for *ref* and enrich it with the Atom title.

        Args:
            ref: Any reference :func:`normalize_arxiv_ref` accepts.

        Returns:
            The PDF bytes, a filesystem-safe ``<id>.pdf`` filename, the
            canonical PDF URL, and the Atom title (``None`` when the metadata
            call fails).

        Raises:
            ValueError: *ref* is not an arXiv reference, or the response body
                is not a PDF.
            httpx.HTTPError: Transport or HTTP-status failure on the PDF GET.
        """
        arxiv_id = normalize_arxiv_ref(ref)
        if arxiv_id is None:
            raise ValueError(f"not an arXiv reference: {ref!r}")
        if self._client is not None:
            return self._fetch(self._client, arxiv_id)
        with httpx.Client(follow_redirects=True, timeout=_TIMEOUT, headers=_HEADERS) as client:
            return self._fetch(client, arxiv_id)

    def _fetch(self, client: httpx.Client, arxiv_id: str) -> Fetched:
        from doc_ray.adapters.document_source import Fetched

        pdf_url = _PDF_URL.format(id=arxiv_id)
        response = client.get(pdf_url)
        response.raise_for_status()
        data = response.content
        if not data.startswith(b"%PDF-"):
            raise ValueError(f"{pdf_url} did not return a PDF (not a PDF payload)")
        return Fetched(
            data=data,
            filename=f"{arxiv_id.replace('/', '-')}.pdf",
            source_url=pdf_url,
            title=self._fetch_title(client, arxiv_id),
        )

    def _fetch_title(self, client: httpx.Client, arxiv_id: str) -> str | None:
        """Return the Atom API title for *arxiv_id*, or ``None`` on any failure."""
        match = _ID.match(arxiv_id)
        base_id = (match.group("new") or match.group("old")) if match else arxiv_id
        try:
            response = client.get(_ATOM_URL, params={"id_list": base_id, "max_results": 1})
            response.raise_for_status()
            root = SafeET.fromstring(response.text)
            node = root.find(_ATOM_TITLE)
            if node is None or not node.text:
                return None
            title = " ".join(node.text.split())
            return title or None
        except Exception:
            return None
