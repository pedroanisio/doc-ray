"""Document sources: ref resolution and the arXiv fetch adapter (mock transport)."""

from __future__ import annotations

import httpx
import pytest

from doc_ray.adapters.document_source import Fetched, resolve_source
from doc_ray.adapters.document_source_arxiv import ArxivSource, normalize_arxiv_ref

FIXTURE_URL = "https://arxiv.org/pdf/2607.20638"

ATOM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2607.20638v1</id>
    <title>Workflow-Localized Mechanism Learning:
      Attribution-Guided   Repair</title>
  </entry>
</feed>
"""


# --- ref normalization -------------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        (FIXTURE_URL, "2607.20638"),
        ("https://arxiv.org/pdf/2607.20638.pdf", "2607.20638"),
        ("https://arxiv.org/abs/2607.20638", "2607.20638"),
        ("https://arxiv.org/abs/2607.20638v2", "2607.20638v2"),
        ("http://www.arxiv.org/abs/2607.20638", "2607.20638"),
        ("https://export.arxiv.org/pdf/2607.20638", "2607.20638"),
        ("arxiv:2607.20638", "2607.20638"),
        ("arXiv:2607.20638v3", "2607.20638v3"),
        ("2607.20638", "2607.20638"),
        ("https://arxiv.org/abs/math.GT/0309136", "math.GT/0309136"),
        ("https://arxiv.org/pdf/math.GT/0309136v1.pdf", "math.GT/0309136v1"),
    ],
)
def test_normalize_accepts_arxiv_refs(ref: str, expected: str) -> None:
    assert normalize_arxiv_ref(ref) == expected


@pytest.mark.parametrize(
    "ref",
    [
        "https://example.com/pdf/2607.20638",  # wrong host
        "https://arxiv.org/list/cs.AI/recent",  # not an abs/pdf path
        "not-an-id",
        "docs/paper.pdf",  # local path
        "12345",  # too short for new-style, no archive for old-style
        "",
    ],
)
def test_normalize_rejects_non_arxiv_refs(ref: str) -> None:
    assert normalize_arxiv_ref(ref) is None


def test_matches_agrees_with_normalize() -> None:
    src = ArxivSource()
    assert src.matches(FIXTURE_URL)
    assert not src.matches("/local/file.pdf")


# --- registry ----------------------------------------------------------------


def test_resolve_source_finds_arxiv_for_fixture_url() -> None:
    src = resolve_source(FIXTURE_URL)
    assert src is not None and src.name == "arxiv"


def test_resolve_source_ignores_local_paths() -> None:
    assert resolve_source("docs/paper.pdf") is None
    assert resolve_source("/abs/path/file.txt") is None


# --- fetch (httpx.MockTransport, no network) ---------------------------------


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def _happy_handler(request: httpx.Request) -> httpx.Response:
    if request.url.host == "arxiv.org" and request.url.path == "/pdf/2607.20638":
        return httpx.Response(200, content=b"%PDF-1.5 fake-pdf-bytes")
    if request.url.host == "export.arxiv.org" and request.url.path == "/api/query":
        assert request.url.params["id_list"] == "2607.20638"
        return httpx.Response(200, text=ATOM_XML)
    return httpx.Response(404)


def test_fetch_returns_pdf_bytes_filename_title_and_url() -> None:
    src = ArxivSource(client=_client(_happy_handler))
    fetched = src.fetch(FIXTURE_URL)
    assert isinstance(fetched, Fetched)
    assert fetched.data.startswith(b"%PDF-")
    assert fetched.filename == "2607.20638.pdf"
    assert fetched.source_url == "https://arxiv.org/pdf/2607.20638"
    # Atom title is whitespace-collapsed to a single line.
    assert fetched.title == "Workflow-Localized Mechanism Learning: Attribution-Guided Repair"


def test_fetch_old_style_id_makes_safe_filename() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/pdf/math.GT/0309136":
            return httpx.Response(200, content=b"%PDF-1.4 x")
        return httpx.Response(500)

    src = ArxivSource(client=_client(handler))
    fetched = src.fetch("https://arxiv.org/abs/math.GT/0309136")
    assert fetched.filename == "math.GT-0309136.pdf"


def test_fetch_rejects_non_pdf_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>captcha</html>")

    src = ArxivSource(client=_client(handler))
    with pytest.raises(ValueError, match="not a PDF"):
        src.fetch(FIXTURE_URL)


def test_fetch_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    src = ArxivSource(client=_client(handler))
    with pytest.raises(httpx.HTTPStatusError):
        src.fetch(FIXTURE_URL)


def test_fetch_survives_title_endpoint_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "arxiv.org":
            return httpx.Response(200, content=b"%PDF-1.5 ok")
        return httpx.Response(503)

    src = ArxivSource(client=_client(handler))
    fetched = src.fetch(FIXTURE_URL)
    assert fetched.data.startswith(b"%PDF-")
    assert fetched.title is None  # metadata failure never blocks the ingest


def test_fetch_rejects_unmatchable_ref() -> None:
    src = ArxivSource(client=_client(_happy_handler))
    with pytest.raises(ValueError, match="not an arXiv reference"):
        src.fetch("https://example.com/x.pdf")


# --- CLI arg splitting (pure, no DB) ------------------------------------------


def test_cli_split_refs_separates_urls_from_paths() -> None:
    from doc_ray.entrypoints.cli import split_refs

    refs, paths = split_refs([FIXTURE_URL, "docs/a.txt", "arxiv:2607.20638", "/tmp/b.pdf"])
    assert refs == [FIXTURE_URL, "arxiv:2607.20638"]
    assert paths == ["docs/a.txt", "/tmp/b.pdf"]
