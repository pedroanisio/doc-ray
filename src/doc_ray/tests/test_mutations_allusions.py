"""End-to-end GraphQL tests for the allusion-edge surface (issue #9).

Drives the real schema via ``schema.execute``: propose a typed cross-lens edge, confirm
it, read it back in both directions, and check the audit trail (success and denial).
An edge never changes identity — that is proven by the DB tests; here we prove the API
contract. Skips without a DB.
"""

from __future__ import annotations

import asyncio
import uuid

from _mutations_common import WRITE_PRINCIPAL, _aconn_as, _seed

from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.schema import build_schema

_PROPOSE = """
mutation ($fc:Int!,$fl:ID!,$tc:Int!,$tl:ID!,$et:String!,$ev:ID!) {
  proposeAllusion(fromCanonicalId:$fc, fromLensId:$fl, toCanonicalId:$tc, toLensId:$tl,
                  edgeType:$et, evidenceDocumentId:$ev) {
    id edgeType status method fromPreferred toPreferred fromLensName toLensName
  }
}
"""
_CONFIRM = "mutation ($id:ID!) { confirmAllusion(id:$id) { id status reviewer } }"
_REJECT = "mutation ($id:ID!) { rejectAllusion(id:$id) { id status } }"
_QUERY = """
query ($cid:Int!,$lens:ID!,$dir:String!) {
  allusions(canonicalId:$cid, lensId:$lens, direction:$dir) {
    id edgeType status toPreferred fromPreferred
  }
}
"""


def _fixture(pg) -> dict:
    conn, _created = pg
    doc = _seed(pg)
    fiction = str(
        conn.execute(
            "INSERT INTO document_collections (name, kind, is_scope, projects) "
            "VALUES (%s,'manual',true,true) RETURNING id",
            (f"novel-{uuid.uuid4().hex[:8]}",),
        ).fetchone()["id"]
    )
    global_lens = str(
        conn.execute("SELECT id FROM document_collections WHERE name='global'").fetchone()["id"]
    )
    local = conn.execute(
        "INSERT INTO canonical (preferred, entity_type) VALUES ('Paris','GPE') RETURNING id"
    ).fetchone()["id"]
    real = conn.execute(
        "INSERT INTO canonical (preferred, entity_type, wikidata_id) VALUES ('Paris','GPE',%s) "
        "RETURNING id",
        (f"Q{uuid.uuid4().hex[:10]}",),
    ).fetchone()["id"]
    return {"doc": doc, "fiction": fiction, "global": global_lens, "local": local, "real": real}


def test_propose_confirm_and_bidirectional_read(pg):
    fx = _fixture(pg)
    schema = build_schema()

    async def go():
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            res = await schema.execute(
                _PROPOSE,
                variable_values={
                    "fc": fx["local"],
                    "fl": fx["fiction"],
                    "tc": fx["real"],
                    "tl": fx["global"],
                    "et": "alludes_to",
                    "ev": fx["doc"],
                },
                context_value=ctx,
            )
            assert res.errors is None, res.errors
            edge = res.data["proposeAllusion"]
            assert edge["edgeType"] == "alludes_to" and edge["status"] == "proposed"
            assert edge["method"] == "human"
            assert edge["toPreferred"] == "Paris" and edge["toLensName"] == "global"
            edge_id = edge["id"]

            # Confirm it — a human verification; still not an identity.
            cres = await schema.execute(
                _CONFIRM, variable_values={"id": edge_id}, context_value=ctx
            )
            assert cres.errors is None, cres.errors
            assert cres.data["confirmAllusion"]["status"] == "confirmed"
            assert cres.data["confirmAllusion"]["reviewer"] == WRITE_PRINCIPAL.principal_id

            # Outgoing from the fiction Paris finds it; incoming does not.
            out = await schema.execute(
                _QUERY,
                variable_values={"cid": fx["local"], "lens": fx["fiction"], "dir": "outgoing"},
                context_value=ctx,
            )
            assert out.errors is None, out.errors
            assert len(out.data["allusions"]) == 1
            assert out.data["allusions"][0]["status"] == "confirmed"
            inc = await schema.execute(
                _QUERY,
                variable_values={"cid": fx["local"], "lens": fx["fiction"], "dir": "incoming"},
                context_value=ctx,
            )
            assert inc.data["allusions"] == []
            # Incoming to the real Paris (global) finds it.
            inc_real = await schema.execute(
                _QUERY,
                variable_values={"cid": fx["real"], "lens": fx["global"], "dir": "incoming"},
                context_value=ctx,
            )
            assert len(inc_real.data["allusions"]) == 1

            # The propose + confirm are audited.
            cur = await aconn.execute(
                "SELECT count(*) AS n FROM audit_events "
                "WHERE action IN ('mutation.propose_allusion','mutation.confirm_allusion') "
                "AND outcome='success'"
            )
            assert (await cur.fetchone())["n"] >= 2
        finally:
            await aconn.close()

    asyncio.run(go())


def test_propose_rejects_an_invalid_edge_type_and_audits_denial(pg):
    fx = _fixture(pg)
    schema = build_schema()

    async def go():
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            res = await schema.execute(
                _PROPOSE,
                variable_values={
                    "fc": fx["local"],
                    "fl": fx["fiction"],
                    "tc": fx["real"],
                    "tl": fx["global"],
                    "et": "is_the_same_as",
                    "ev": fx["doc"],  # smells like identity — rejected
                },
                context_value=ctx,
            )
            assert res.errors is not None  # nothing created
            cur = await aconn.execute(
                "SELECT count(*) AS n FROM audit_events "
                "WHERE action='mutation.propose_allusion' AND outcome='denied'"
            )
            assert (await cur.fetchone())["n"] >= 1
        finally:
            await aconn.close()

    asyncio.run(go())


def test_reject_marks_edge_rejected(pg):
    fx = _fixture(pg)
    schema = build_schema()

    async def go():
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            res = await schema.execute(
                _PROPOSE,
                variable_values={
                    "fc": fx["local"],
                    "fl": fx["fiction"],
                    "tc": fx["real"],
                    "tl": fx["global"],
                    "et": "depicts",
                    "ev": fx["doc"],
                },
                context_value=ctx,
            )
            edge_id = res.data["proposeAllusion"]["id"]
            rres = await schema.execute(_REJECT, variable_values={"id": edge_id}, context_value=ctx)
            assert rres.errors is None, rres.errors
            assert rres.data["rejectAllusion"]["status"] == "rejected"
        finally:
            await aconn.close()

    asyncio.run(go())
