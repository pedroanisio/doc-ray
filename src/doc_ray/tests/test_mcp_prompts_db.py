"""Integration test for summarize_document against a live DB (skip without one).

FR-2 is the whole justification for the prompts primitive: summarize_document must
be parameterized by the document's *real* data, not static boilerplate. The unit
tests prove the composition against stubbed tool responses; this proves it against
a real corpus, routing the prompt's tool calls (get_document, read_document_outline)
through the in-process Strawberry schema exactly as the evaluation harness does
(tool_transport). It seeds a headings document and a heading-less one and asserts
the plans differ, address the real ordinals, and stay honest about `source`.
"""

from __future__ import annotations

import asyncio

from doc_ray.application.mcp_eval import (
    HARNESS_PRINCIPAL,
    HARNESS_PRINCIPAL_ID,
    default_aconn_factory,
    tool_transport,
)
from doc_ray.entrypoints.api.schema import build_schema
from doc_ray.entrypoints.mcp import corpus, prompts

_WITH_HEADINGS = (
    "# Book One\n"
    "Paul walked the sand. The spice flowed around him.\n"
    "## Chapter Two\n"
    "Jessica watched from the ledge. She said nothing at all.\n"
    "### Chapter Three\n"
    "Duncan Idaho returned. The ghola remembered everything.\n"
)

_NO_HEADINGS = (
    "Paul walked the sand. The spice flowed around him. "
    "Jessica watched from the ledge. She said nothing at all. "
    "Duncan Idaho returned. The ghola remembered everything."
)


def _seed(pg, text: str) -> str:
    import _itdb

    conn, created = pg
    # The prompt reads through the schema as HARNESS_PRINCIPAL (docray_api); make it
    # an admin in the default tenant so it can see the seeded document.
    _itdb.ensure_admin_principal(conn, HARNESS_PRINCIPAL_ID, "mcp-eval-harness")
    doc_id = _itdb.make_doc(conn, text)
    created.append(doc_id)
    return doc_id


async def _summary(document_id: str) -> str:
    schema = build_schema()
    corpus.caller_token.set("integration-test")
    async with tool_transport(
        schema=schema, principal=HARNESS_PRINCIPAL, aconn_factory=default_aconn_factory
    ):
        return await prompts.summarize_document(document_id)


def test_summarize_document_is_parameterized_by_the_real_document(pg):
    doc_id = _seed(pg, _WITH_HEADINGS)

    async def check():
        msg = await _summary(doc_id)
        # Real detected headings and the real sentence count show up in the plan.
        assert "Book One" in msg
        assert "Chapter Two" in msg
        assert "read_document_sentences" in msg
        assert "ordinal" in msg.lower()
        # A headings outline is labelled as real structure, not a grid.
        assert "headings" in msg.lower()

    asyncio.run(check())


def test_summarize_document_differs_between_two_documents_and_is_honest_about_source(pg):
    headings_doc = _seed(pg, _WITH_HEADINGS)
    grid_doc = _seed(pg, _NO_HEADINGS)

    async def check():
        headings_plan = await _summary(headings_doc)
        grid_plan = await _summary(grid_doc)
        # FR-2 regression: not the same boilerplate for two different documents.
        assert headings_plan != grid_plan
        # FR-3: the heading-less document is disclosed as a uniform grid whose titles
        # are not chapter names — never laundered into a table of contents.
        low = grid_plan.lower()
        assert "uniform" in low or "even split" in low
        assert "not chapter" in low

    asyncio.run(check())
