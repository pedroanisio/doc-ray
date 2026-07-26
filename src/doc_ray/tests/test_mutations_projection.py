"""End-to-end GraphQL tests for the Block D projection surface (#8).

Drives the real schema via ``schema.execute`` with a ``RequestContext``: read a
document's lens-scoped projection and a lens's entities, designate a lens as
projecting, and confirm a projection (record-then-apply — the mutation enqueues a
refresh that the worker applies). Skips without a DB.
"""

from __future__ import annotations

import asyncio
import uuid

from _mutations_common import WRITE_PRINCIPAL, _aconn_as, _seed

from doc_ray.application import projection as ap
from doc_ray.domain.ulid import new_ulid_str
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.schema import build_schema

_READ = """
query ($id: ID!, $lens: ID) {
  entityProjection(documentId: $id, lensId: $lens) {
    lensId isDefault ambiguous
    entities { mentionId scope method status canonicalId surface preferred }
  }
}
"""
_LENS_ENTITIES = """
query ($lens: ID!) {
  lensEntities(lensId: $lens) { canonicalId preferred scope nMentions nDocuments }
}
"""
_SET_PROJECTS = (
    "mutation ($lens: ID!, $p: Boolean!) { "
    "setLensProjects(lensId: $lens, projects: $p) { id projects isScope } }"
)
_CONFIRM = (
    "mutation ($m: ID!, $lens: ID!) { "
    "confirmProjection(mentionId: $m, lensId: $lens) { id kind status documentId } }"
)


def _lens(conn, name: str, *, projects: bool = True) -> str:
    return str(
        conn.execute(
            "INSERT INTO document_collections (name, kind, is_scope, projects) "
            "VALUES (%s, 'manual', true, %s) RETURNING id",
            (f"{name}-{uuid.uuid4().hex[:8]}", projects),
        ).fetchone()["id"]
    )


def _annotate_one(conn, doc_id: str, surface: str = "Duncan", etype: str = "PERSON") -> str:
    ann = new_ulid_str()
    conn.execute(
        "INSERT INTO annotations (id, document_id, layer, tool) VALUES (%s,%s,'spacy','test')",
        (ann, doc_id),
    )
    span = conn.execute(
        "INSERT INTO spans (document_id, char_start, char_end) VALUES (%s,0,6) "
        "ON CONFLICT (document_id, char_start, char_end) DO UPDATE SET char_start = 0 RETURNING id",
        (doc_id,),
    ).fetchone()["id"]
    mid = new_ulid_str()
    conn.execute(
        "INSERT INTO entity_mention (id, annotation_id, span_id, entity_type, surface) "
        "VALUES (%s,%s,%s,%s,%s)",
        (mid, ann, span, etype, surface),
    )
    return mid


def _fiction_doc_with_lens(pg) -> tuple[str, str, str]:
    conn, _created = pg
    doc_id = _seed(pg)
    lens = _lens(conn, "work")
    conn.execute(
        "INSERT INTO document_collection_members (collection_id, document_id) VALUES (%s,%s) "
        "ON CONFLICT DO NOTHING",
        (lens, doc_id),
    )
    conn.execute(
        "INSERT INTO document_classification (document_id, genre, method, status) "
        "VALUES (%s,'fiction','heuristic','pending') ON CONFLICT (document_id) DO UPDATE "
        "SET genre='fiction'",
        (doc_id,),
    )
    mid = _annotate_one(conn, doc_id)
    ap.refresh_projections_for_document(conn, doc_id)
    return doc_id, lens, mid


def test_entity_projection_read_through_a_lens(pg):
    doc_id, lens, _mid = _fiction_doc_with_lens(pg)
    schema = build_schema()

    async def check():
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            res = await schema.execute(
                _READ, variable_values={"id": doc_id, "lens": lens}, context_value=ctx
            )
            assert res.errors is None, res.errors
            proj = res.data["entityProjection"]
            assert proj["lensId"] == lens
            assert len(proj["entities"]) == 1
            e = proj["entities"][0]
            assert e["scope"] == "local" and e["status"] == "proposed"
            assert e["surface"] == "Duncan" and e["canonicalId"] is not None
        finally:
            await aconn.close()

    asyncio.run(check())


def test_lens_entities_read(pg):
    _doc, lens, _mid = _fiction_doc_with_lens(pg)
    schema = build_schema()

    async def check():
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            res = await schema.execute(
                _LENS_ENTITIES, variable_values={"lens": lens}, context_value=ctx
            )
            assert res.errors is None, res.errors
            ents = res.data["lensEntities"]
            assert len(ents) == 1
            assert ents[0]["preferred"] == "Duncan" and ents[0]["scope"] == "local"
            assert ents[0]["nMentions"] == 1
        finally:
            await aconn.close()

    asyncio.run(check())


def test_set_lens_projects_toggles_designation(pg):
    conn, _created = pg
    _seed(pg)  # ensures admin principal + jobs table
    lens = _lens(conn, "toggle", projects=False)
    schema = build_schema()

    async def check():
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            res = await schema.execute(
                _SET_PROJECTS, variable_values={"lens": lens, "p": True}, context_value=ctx
            )
            assert res.errors is None, res.errors
            assert res.data["setLensProjects"]["projects"] is True
            assert res.data["setLensProjects"]["isScope"] is True
        finally:
            await aconn.close()

    asyncio.run(check())


def test_confirm_projection_records_review_then_worker_applies(pg):
    conn, _created = pg
    doc_id, lens, mid = _fiction_doc_with_lens(pg)
    schema = build_schema()

    async def confirm():
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            res = await schema.execute(
                _CONFIRM, variable_values={"m": mid, "lens": lens}, context_value=ctx
            )
            assert res.errors is None, res.errors
            job = res.data["confirmProjection"]
            assert job["kind"] == "refresh_projection"
            assert job["status"] == "queued" and job["documentId"] == doc_id
            # The review row was recorded (record-then-apply); status not yet applied.
            cur = await aconn.execute(
                "SELECT decision FROM entity_projection_review "
                "WHERE document_id = %s AND lens_id = %s",
                (doc_id, lens),
            )
            assert (await cur.fetchone())["decision"] == "confirmed"
        finally:
            await aconn.close()

    asyncio.run(confirm())

    # The worker applies the review onto the projection.
    from doc_ray.application import job_handlers
    from doc_ray.domain.jobs import KIND_REFRESH_PROJECTION, Job

    job_handlers.handle_refresh_projection(
        conn,
        None,
        Job(
            id=new_ulid_str(),
            kind=KIND_REFRESH_PROJECTION,
            status="running",
            document_id=doc_id,
            payload={"lens_id": lens},
        ),
    )
    row = conn.execute(
        "SELECT status FROM entity_projection WHERE mention_id = %s AND lens_id = %s", (mid, lens)
    ).fetchone()
    assert row["status"] == "confirmed", "the confirmation was applied by the refresh"
