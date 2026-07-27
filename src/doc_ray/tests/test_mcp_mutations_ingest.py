"""The MCP ``ingest_document`` write tool (D-015 split from the mutation suite).

The one intake verb on MCP: an agent has no file transport, so a reference is its
only route into the corpus. Same no-network contract as the other write tools.
"""

from __future__ import annotations

import pytest

from doc_ray.entrypoints.mcp import corpus, mutations
from doc_ray.tests.test_mcp_mutations import _patch, _reset_token  # noqa: F401

_INGEST_JOB = {"id": "job-ref", "kind": "ingest", "status": "queued", "documentId": None}


@pytest.mark.asyncio
async def test_ingest_document_forwards_the_reference_and_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The agent-facing half of ingest-by-reference: one ref in, one Job out.

    MCP has no file transport, so the reference route is the only intake an
    agent can drive — without it the arXiv source is reachable from a shell and
    nowhere else.
    """
    client = _patch(monkeypatch, {"data": {"ingestDocument": _INGEST_JOB}})
    corpus.caller_token.set("tok-write")

    out = await mutations.ingest_document("https://arxiv.org/abs/2401.00001")

    assert out == _INGEST_JOB
    _url, body, headers = client.calls[0]
    assert headers["Authorization"] == "Bearer tok-write"
    assert "ingestDocument(ref:$ref,title:$title,pipeline:$pipeline)" in body["query"]
    assert body["query"].lstrip().startswith("mutation")
    assert body["variables"] == {
        "ref": "https://arxiv.org/abs/2401.00001",
        "title": None,
        "pipeline": True,
    }


@pytest.mark.asyncio
async def test_ingest_document_passes_an_explicit_title_and_pipeline_opt_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _patch(monkeypatch, {"data": {"ingestDocument": _INGEST_JOB}})
    corpus.caller_token.set("tok-write")

    await mutations.ingest_document("arxiv:2401.00002", title="Chosen", pipeline=False)

    _url, body, _headers = client.calls[0]
    assert body["variables"] == {"ref": "arxiv:2401.00002", "title": "Chosen", "pipeline": False}


@pytest.mark.asyncio
async def test_ingest_document_rejects_an_empty_reference() -> None:
    """Validated locally, before a pointless round trip."""
    with pytest.raises(corpus.ToolCallError):
        await mutations.ingest_document("   ")


def test_ingest_document_is_registered_as_a_write_tool() -> None:
    """It must ship behind DOCRAY_MCP_ENABLE_WRITE like every other mutation."""
    assert mutations.ingest_document in mutations.TOOLS
