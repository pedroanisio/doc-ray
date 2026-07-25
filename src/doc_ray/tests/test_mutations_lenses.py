"""End-to-end GraphQL tests for the Lens Registry (Block B, issue #5).

Drives the real Strawberry schema via ``schema.execute`` with a ``RequestContext``
(the pattern in ``test_mutations_ner_corrections.py``): promote a collection to a
lens, add a document and set it as the default, read it back through ``lenses`` and
``document.defaultLensId``, and prove demote-in-use is rejected. Each path is
checked for the audit event it must emit on success and on denial. Skips without a
safe test database.
"""

from __future__ import annotations

import asyncio

from _mutations_common import WRITE_PRINCIPAL, _aconn_as, _seed

from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.schema import build_schema

_CREATE = """
mutation ($n: String!) { createCollection(name: $n) { id isScope } }
"""
_PROMOTE = """
mutation ($c: ID!, $p: ID) {
  promoteCollectionToLens(collectionId: $c, parentLensId: $p) { id isScope parentLensId }
}
"""
_DEMOTE = "mutation ($c: ID!) { demoteLens(collectionId: $c) { id isScope } }"
_ADD = """
mutation ($c: ID!, $d: ID!) { addDocumentToCollection(collectionId: $c, documentId: $d) { id } }
"""
_SET_DEFAULT = """
mutation ($d: ID!, $l: ID) {
  setDocumentDefaultLens(documentId: $d, lensId: $l) { id defaultLensId }
}
"""
_LENSES = "query { lenses(limit: 100) { id name isScope } }"


def _audit_outcomes(conn, action: str) -> list[str]:
    rows = conn.execute(
        "SELECT outcome FROM audit_events WHERE action = %s ORDER BY occurred_at", (action,)
    ).fetchall()
    return [r["outcome"] for r in rows]


async def _exec(schema, ctx, doc, variables):
    return await schema.execute(doc, variable_values=variables, context_value=ctx)


def test_promote_set_default_and_read_back(pg) -> None:
    conn, _created = pg
    doc_id = _seed(pg)
    schema = build_schema()
    unique = f"Dune Series {doc_id[:8]}"

    async def check() -> None:
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))

            created = await _exec(schema, ctx, _CREATE, {"n": unique})
            assert created.errors is None, created.errors
            assert created.data["createCollection"]["isScope"] is False
            coll_id = created.data["createCollection"]["id"]

            promoted = await _exec(schema, ctx, _PROMOTE, {"c": coll_id, "p": None})
            assert promoted.errors is None, promoted.errors
            assert promoted.data["promoteCollectionToLens"]["isScope"] is True

            # The lens (and the reserved global lens) show up in `lenses`.
            listed = await _exec(schema, ctx, _LENSES, {})
            assert listed.errors is None, listed.errors
            names = {row["name"] for row in listed.data["lenses"]}
            assert unique in names and "global" in names
            assert all(row["isScope"] for row in listed.data["lenses"])

            add = await _exec(schema, ctx, _ADD, {"c": coll_id, "d": doc_id})
            assert add.errors is None, add.errors

            setd = await _exec(schema, ctx, _SET_DEFAULT, {"d": doc_id, "l": coll_id})
            assert setd.errors is None, setd.errors
            assert setd.data["setDocumentDefaultLens"]["defaultLensId"] == coll_id

            # Demote-in-use is rejected while the document still defaults to it.
            denied = await _exec(schema, ctx, _DEMOTE, {"c": coll_id})
            assert denied.errors is not None
            assert "default" in str(denied.errors[0]).lower()

            # Clear the default, then demotion succeeds.
            cleared = await _exec(schema, ctx, _SET_DEFAULT, {"d": doc_id, "l": None})
            assert cleared.errors is None, cleared.errors
            assert cleared.data["setDocumentDefaultLens"]["defaultLensId"] is None
            demoted = await _exec(schema, ctx, _DEMOTE, {"c": coll_id})
            assert demoted.errors is None, demoted.errors
            assert demoted.data["demoteLens"]["isScope"] is False
        finally:
            await aconn.close()

    asyncio.run(check())

    # Audit trail: promotion succeeded; the in-use demote was denied then a demote
    # succeeded — every durable write and its denial is recorded.
    assert "success" in _audit_outcomes(conn, "mutation.promote_collection_to_lens")
    demote_outcomes = _audit_outcomes(conn, "mutation.demote_lens")
    assert "denied" in demote_outcomes and "success" in demote_outcomes
    assert "success" in _audit_outcomes(conn, "mutation.set_document_default_lens")


def test_set_default_to_non_member_is_rejected(pg) -> None:
    conn, _created = pg
    doc_id = _seed(pg)
    schema = build_schema()
    unique = f"Non-member lens {doc_id[:8]}"

    async def check() -> None:
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            created = await _exec(schema, ctx, _CREATE, {"n": unique})
            coll_id = created.data["createCollection"]["id"]
            await _exec(schema, ctx, _PROMOTE, {"c": coll_id, "p": None})
            # doc is NOT a member of the lens -> setting it as default is rejected.
            res = await _exec(schema, ctx, _SET_DEFAULT, {"d": doc_id, "l": coll_id})
            assert res.errors is not None
            assert "member" in str(res.errors[0]).lower()
        finally:
            await aconn.close()

    asyncio.run(check())
    assert "denied" in _audit_outcomes(conn, "mutation.set_document_default_lens")


def test_parent_cycle_is_rejected_through_graphql(pg) -> None:
    conn, _created = pg
    doc_id = _seed(pg)
    schema = build_schema()
    a_name, b_name = f"Franchise A {doc_id[:8]}", f"Franchise B {doc_id[:8]}"

    async def check() -> None:
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            a = (await _exec(schema, ctx, _CREATE, {"n": a_name})).data["createCollection"]["id"]
            b = (await _exec(schema, ctx, _CREATE, {"n": b_name})).data["createCollection"]["id"]
            await _exec(schema, ctx, _PROMOTE, {"c": a, "p": None})
            # b under a is fine...
            ok = await _exec(schema, ctx, _PROMOTE, {"c": b, "p": a})
            assert ok.errors is None, ok.errors
            assert ok.data["promoteCollectionToLens"]["parentLensId"] == a
            # ...but a under b closes a -> b -> a and must be rejected.
            cyc = await _exec(schema, ctx, _PROMOTE, {"c": a, "p": b})
            assert cyc.errors is not None
            assert "cycle" in str(cyc.errors[0]).lower()
        finally:
            await aconn.close()

    asyncio.run(check())


def test_promote_requires_write_scope(pg) -> None:
    """An unauthenticated principal cannot promote — and the denial is audited."""
    conn, _created = pg
    doc_id = _seed(pg)
    schema = build_schema()
    unique = f"Guarded lens {doc_id[:8]}"

    async def check() -> None:
        writer = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        anon = await _aconn_as(None)
        try:
            wctx = RequestContext(conn=writer, principal=WRITE_PRINCIPAL, loaders=Loaders(writer))
            coll_id = (await _exec(schema, wctx, _CREATE, {"n": unique})).data["createCollection"][
                "id"
            ]
            from doc_ray.entrypoints.api.security import Principal

            anon_principal = Principal(subject="anon", principal_id=None, scopes=frozenset())
            actx = RequestContext(conn=anon, principal=anon_principal, loaders=Loaders(anon))
            res = await _exec(schema, actx, _PROMOTE, {"c": coll_id, "p": None})
            assert res.errors is not None  # permission denied
        finally:
            await writer.close()
            await anon.close()

    asyncio.run(check())
    assert "denied" in _audit_outcomes(conn, "mutation.promote_collection_to_lens")
