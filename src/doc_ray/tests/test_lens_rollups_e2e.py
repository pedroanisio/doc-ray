"""End-to-end GraphQL tests for the hyper-context surface (Block F, #10).

Drives the real schema via ``schema.execute``: read a lens's ranked entities (with type
profile + staleness), an entity's co-occurrence relations, and bounded N-hop discovery —
and prove the hop cap is enforced by **rejection**, not silent clamping. Skips without a DB.
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

_ENTITIES = """
query ($lens:ID!,$min:Int!) {
  lensEntities(lensId:$lens, minMentions:$min) {
    canonicalId preferred nMentions nDocuments typeProfile typeConsistency
    firstSeenOrdinal scope refreshedAt
  }
}
"""
_RELATIONS = """
query ($lens:ID!,$cid:Int!) {
  entityRelations(lensId:$lens, canonicalId:$cid) {
    canonicalId preferred cooccurrenceCount strength refreshedAt
  }
}
"""
_RELATED = """
query ($lens:ID!,$cid:Int!,$h:Int!) {
  relatedEntities(lensId:$lens, canonicalId:$cid, hops:$h) { canonicalId preferred hops }
}
"""


def _seed_lens(pg) -> str:
    conn, _created = pg
    doc = _seed(pg)
    lens = str(
        conn.execute(
            "INSERT INTO document_collections (name, kind, is_scope, projects) "
            "VALUES (%s,'manual',true,true) RETURNING id",
            (f"saga-{uuid.uuid4().hex[:8]}",),
        ).fetchone()["id"]
    )
    conn.execute("DELETE FROM sentences WHERE document_id = %s", (doc,))
    conn.execute(
        "INSERT INTO document_collection_members (collection_id, document_id) VALUES (%s,%s)",
        (lens, doc),
    )
    conn.execute(
        "INSERT INTO document_classification (document_id, genre, method, status) "
        "VALUES (%s,'fiction','heuristic','pending')",
        (doc,),
    )
    for ordv, (a, b) in enumerate([(0, 10), (10, 20)]):
        span = conn.execute(
            "INSERT INTO spans (document_id, char_start, char_end) VALUES (%s,%s,%s) RETURNING id",
            (doc, a, b),
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO sentences (id, document_id, ordinal, text, span_id) VALUES (%s,%s,%s,%s,%s)",
            (new_ulid_str(), doc, ordv, "s", span),
        )
    ann = new_ulid_str()
    conn.execute(
        "INSERT INTO annotations (id, document_id, layer, tool) VALUES (%s,%s,'spacy','test')",
        (ann, doc),
    )
    for cstart, cend, surface in [
        (0, 1, "Alpha"),
        (2, 3, "Beta"),
        (10, 11, "Alpha"),
        (12, 13, "Beta"),
    ]:
        span = conn.execute(
            "INSERT INTO spans (document_id, char_start, char_end) VALUES (%s,%s,%s) RETURNING id",
            (doc, cstart, cend),
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO entity_mention (id, annotation_id, span_id, entity_type, surface) "
            "VALUES (%s,%s,%s,'PERSON',%s)",
            (new_ulid_str(), ann, span, surface),
        )
    ap.refresh_projections_for_document(conn, doc)  # projects + chains the rollup refresh
    return lens


def test_hyper_context_reads_end_to_end(pg):
    lens = _seed_lens(pg)
    schema = build_schema()

    async def go():
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            res = await schema.execute(
                _ENTITIES, variable_values={"lens": lens, "min": 0}, context_value=ctx
            )
            assert res.errors is None, res.errors
            ents = {e["preferred"]: e for e in res.data["lensEntities"]}
            assert ents["Alpha"]["nMentions"] == 2 and ents["Alpha"]["scope"] == "local"
            assert ents["Alpha"]["typeProfile"] == {"PERSON": 2}
            assert ents["Alpha"]["typeConsistency"] == 1.0
            assert ents["Alpha"]["refreshedAt"] is not None
            alpha_id = ents["Alpha"]["canonicalId"]

            # minMentions filters Alpha/Beta (both have 2) out at min=3.
            filtered = await schema.execute(
                _ENTITIES, variable_values={"lens": lens, "min": 3}, context_value=ctx
            )
            assert filtered.data["lensEntities"] == []

            rel = await schema.execute(
                _RELATIONS, variable_values={"lens": lens, "cid": alpha_id}, context_value=ctx
            )
            assert rel.errors is None, rel.errors
            assert len(rel.data["entityRelations"]) == 1
            assert rel.data["entityRelations"][0]["preferred"] == "Beta"
            assert rel.data["entityRelations"][0]["cooccurrenceCount"] == 2

            related = await schema.execute(
                _RELATED, variable_values={"lens": lens, "cid": alpha_id, "h": 2}, context_value=ctx
            )
            assert related.errors is None, related.errors
            assert [r["preferred"] for r in related.data["relatedEntities"]] == ["Beta"]
            assert related.data["relatedEntities"][0]["hops"] == 1

            # Hop cap is enforced by rejection, not clamping.
            over = await schema.execute(
                _RELATED,
                variable_values={"lens": lens, "cid": alpha_id, "h": 99},
                context_value=ctx,
            )
            assert over.errors is not None
        finally:
            await aconn.close()

    asyncio.run(go())
