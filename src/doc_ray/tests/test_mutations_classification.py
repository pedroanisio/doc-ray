"""End-to-end GraphQL tests for the Block C genre-classification surface (#6).

A curator enqueues ``classifyDocument`` (the worker writes a pending machine
guess), reads it back via ``documentClassification``, and confirms it with
``confirmDocumentClassification`` (a human decision, audited). Skips without a DB.
"""

from __future__ import annotations

import asyncio

from _mutations_common import WRITE_PRINCIPAL, _aconn_as, _seed

from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.schema import build_schema

_CLASSIFY = (
    "mutation ($id: ID!) { classifyDocument(documentId: $id) { id kind status documentId } }"
)
_CONFIRM = """
mutation ($id: ID!, $g: String!) {
  confirmDocumentClassification(documentId: $id, genre: $g, subtype: "novel", rationale: "human call") {
    documentId genre subtype status method reviewer
  }
}
"""
_READ = """
query ($id: ID!) {
  documentClassification(documentId: $id) { genre status method confidence }
}
"""

_NOVEL = """Chapter One
"You can't be serious," she said. I had waited my whole life for this moment.
Chapter Two
"Where will you go?" I asked. My heart raced as I remembered everything.
"""


def _novel_doc(pg) -> str:
    conn, _created = pg
    doc_id = _seed(pg)
    conn.execute(
        "UPDATE document_content SET full_text = %s WHERE document_id = %s", (_NOVEL, doc_id)
    )
    return doc_id


def test_classify_document_enqueues_then_worker_writes_pending(pg):
    conn, _created = pg
    doc_id = _novel_doc(pg)
    schema = build_schema()

    async def enqueue():
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            res = await schema.execute(_CLASSIFY, variable_values={"id": doc_id}, context_value=ctx)
            assert res.errors is None, res.errors
            job = res.data["classifyDocument"]
            assert job["kind"] == "classify_genre"
            assert job["status"] == "queued"
            assert job["documentId"] == doc_id
            # Not applied yet (async job).
            r0 = await schema.execute(_READ, variable_values={"id": doc_id}, context_value=ctx)
            assert r0.data["documentClassification"] is None
        finally:
            await aconn.close()

    asyncio.run(enqueue())

    # The worker runs the classifier and writes a pending machine guess.
    from doc_ray.application import job_handlers
    from doc_ray.domain.jobs import KIND_CLASSIFY_GENRE, Job
    from doc_ray.domain.ulid import new_ulid_str

    out = job_handlers.handle_classify_genre(
        conn,
        None,
        Job(id=new_ulid_str(), kind=KIND_CLASSIFY_GENRE, status="running", document_id=doc_id),
    )
    assert out["genre"] == "fiction"
    assert out["status"] == "pending"

    async def read_back():
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            r = await schema.execute(_READ, variable_values={"id": doc_id}, context_value=ctx)
            assert r.errors is None, r.errors
            dc = r.data["documentClassification"]
            assert dc["genre"] == "fiction"
            assert dc["status"] == "pending"  # a machine guess is never asserted as confirmed
            assert dc["method"] == "heuristic"
        finally:
            await aconn.close()

    asyncio.run(read_back())


def test_confirm_document_classification_overrides_and_is_audited(pg):
    doc_id = _novel_doc(pg)
    schema = build_schema()

    async def check():
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            res = await schema.execute(
                _CONFIRM, variable_values={"id": doc_id, "g": "non_fiction"}, context_value=ctx
            )
            assert res.errors is None, res.errors
            dc = res.data["confirmDocumentClassification"]
            assert dc["genre"] == "non_fiction"
            assert dc["subtype"] == "novel"
            assert dc["status"] == "confirmed"
            assert dc["method"] == "human"
            assert dc["reviewer"] == WRITE_PRINCIPAL.principal_id

            # The confirmation is audited (durable state change).
            cur = await aconn.execute(
                "SELECT count(*) AS n FROM audit_events "
                "WHERE action = %s AND target_id = %s AND outcome = 'success'",
                ("mutation.confirm_document_classification", doc_id),
            )
            row = await cur.fetchone()
            assert row["n"] >= 1
        finally:
            await aconn.close()

    asyncio.run(check())


def test_confirm_rejects_an_invalid_genre(pg):
    doc_id = _novel_doc(pg)
    schema = build_schema()

    async def check():
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            res = await schema.execute(
                _CONFIRM, variable_values={"id": doc_id, "g": "sci_fi"}, context_value=ctx
            )
            assert res.errors is not None  # not in the CHECK set; nothing confirmed
        finally:
            await aconn.close()

    asyncio.run(check())
