"""End-to-end GraphQL tests for the Block A NER-correction surface.

A curator records a correction through ``correctEntityMention`` (which enqueues a
cheap ``apply_corrections`` job — entity_mention is worker-owned, so the API only
records the log), reads it back via ``entityMentionCorrections``, and the worker
handler re-applies it onto the document's ML mentions. Skips without a DB.
"""

from __future__ import annotations

import asyncio

from _mutations_common import WRITE_PRINCIPAL, _aconn_as, _seed

from doc_ray.adapters import persistence as db
from doc_ray.domain.ulid import new_ulid_str
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.schema import build_schema

_CORRECT = """
mutation ($id: ID!) {
  correctEntityMention(
    documentId: $id, action: "reclassify",
    charStart: 16, charEnd: 22, entityType: "GPE",
    originalType: "PERSON", rationale: "London is a place"
  ) { id kind status documentId }
}
"""

_LIST = """
query ($id: ID!) {
  entityMentionCorrections(documentId: $id) {
    action charStart charEnd entityType originalType rationale
  }
}
"""


def _seed_mention(pg) -> tuple[str, str]:
    """Seed a document with one (mislabeled) ML entity mention; return ids."""
    import _itdb

    conn, _created = pg
    doc_id = _seed(pg)
    # "Reannotate me. Second sentence." — force a known span/text for the fixture.
    conn.execute(
        "UPDATE document_content SET full_text = %s WHERE document_id = %s",
        ("Winston lived in London today.", doc_id),
    )
    ann_id = _itdb.make_annotation(conn, doc_id, "entity", "spacy/test")
    span_id = db.ensure_spans(conn, doc_id, [(16, 22)])[(16, 22)]
    conn.execute(
        "INSERT INTO entity_mention (id, annotation_id, span_id, entity_type, surface) "
        "VALUES (%s, %s, %s, %s, %s)",
        (new_ulid_str(), ann_id, span_id, "PERSON", "London"),
    )
    return doc_id, ann_id


def _entity_type(conn, ann_id: str, span=(16, 22)) -> str | None:
    row = conn.execute(
        "SELECT em.entity_type AS t FROM entity_mention em JOIN spans sp ON sp.id = em.span_id "
        "WHERE em.annotation_id = %s AND sp.char_start = %s AND sp.char_end = %s",
        (ann_id, span[0], span[1]),
    ).fetchone()
    return row["t"] if row else None


def test_correct_entity_mention_records_and_enqueues(pg):
    conn, _created = pg
    doc_id, ann_id = _seed_mention(pg)
    schema = build_schema()

    async def check():
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            res = await schema.execute(_CORRECT, variable_values={"id": doc_id}, context_value=ctx)
            assert res.errors is None, res.errors
            job = res.data["correctEntityMention"]
            assert job["kind"] == "apply_corrections"
            assert job["status"] == "queued"
            assert job["documentId"] == doc_id

            listed = await schema.execute(_LIST, variable_values={"id": doc_id}, context_value=ctx)
            assert listed.errors is None, listed.errors
            corrections = listed.data["entityMentionCorrections"]
            assert len(corrections) == 1
            assert corrections[0]["action"] == "reclassify"
            assert corrections[0]["entityType"] == "GPE"
            assert corrections[0]["originalType"] == "PERSON"
        finally:
            await aconn.close()

    asyncio.run(check())

    # The worker applies it: the mention flips PERSON -> GPE and survives.
    from doc_ray.application import job_handlers
    from doc_ray.domain.jobs import KIND_APPLY_CORRECTIONS, Job

    assert _entity_type(conn, ann_id) == "PERSON"  # not yet applied (async job)
    job = Job(id=new_ulid_str(), kind=KIND_APPLY_CORRECTIONS, status="running", document_id=doc_id)
    out = job_handlers.handle_apply_corrections(conn, None, job)
    assert out["corrections_applied"] is True
    assert _entity_type(conn, ann_id) == "GPE"


def test_correct_entity_mention_rejects_unknown_action(pg):
    doc_id, _ann_id = _seed_mention(pg)
    schema = build_schema()

    async def check():
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            res = await schema.execute(
                "mutation ($id: ID!) { correctEntityMention("
                'documentId: $id, action: "obliterate", charStart: 0, charEnd: 3'
                ") { id } }",
                variable_values={"id": doc_id},
                context_value=ctx,
            )
            assert res.errors is not None  # invalid action rejected, nothing recorded
        finally:
            await aconn.close()

    asyncio.run(check())
