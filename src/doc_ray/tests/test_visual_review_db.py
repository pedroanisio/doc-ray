"""DB-backed GraphQL review loop for untrusted Tier-3 visual captions."""

from __future__ import annotations

import asyncio
import hashlib
import uuid

import _itdb
from _mutations_common import WRITE_PRINCIPAL, _aconn_as, _seed
from psycopg.types.json import Jsonb

from doc_ray.domain.ulid import new_ulid_str
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.schema import build_schema
from doc_ray.entrypoints.api.security import Principal

_LIST = """
query ($d: ID) {
  visualReview(documentId: $d, status: "pending", limit: 50) {
    id documentImageId proposed groundingScore groundingEvidence pageNumber objectKey cursor
  }
}
"""
_DECIDE = """
mutation ($a: [ID!], $r: [ID!]) {
  decideVisualCaptions(approveIds: $a, rejectIds: $r)
}
"""
_CAPTIONS = """
query ($d: ID!) { visualCaptions(documentId: $d) { caption groundingScore } }
"""


def _proposal(conn, doc_id: str, caption: str) -> str:
    image_id = new_ulid_str()
    review_id = new_ulid_str()
    digest = hashlib.sha256(image_id.encode()).hexdigest()
    conn.execute(
        "INSERT INTO document_image "
        "(id, document_id, page_number, source, width_px, height_px, object_key, sha256, "
        " detector, detector_version) "
        "VALUES (%s, %s, 1, 'full-page', 100, 80, %s, %s, 'itest', '1')",
        (image_id, doc_id, f"document-images/{doc_id}/{image_id}.png", digest),
    )
    conn.execute(
        "INSERT INTO visual_caption "
        "(id, document_image_id, document_id, caption, analysis, model, model_version, "
        " grounding_score, grounded_against, status) "
        "VALUES (%s, %s, %s, %s, %s, 'itest-vlm', '1', 0.72, %s, 'review')",
        (
            new_ulid_str(),
            image_id,
            doc_id,
            caption,
            Jsonb({"source": "itest"}),
            Jsonb({"matched_terms": ["diagram"]}),
        ),
    )
    conn.execute(
        "INSERT INTO visual_review "
        "(id, document_image_id, document_id, proposed, grounding_score, "
        " grounding_evidence, status) "
        "VALUES (%s, %s, %s, %s, 0.72, %s, 'pending')",
        (
            review_id,
            image_id,
            doc_id,
            Jsonb({"caption": caption, "analysis": {"source": "itest"}}),
            Jsonb({"matched_terms": ["diagram"]}),
        ),
    )
    return review_id


def test_visual_review_approve_reject_and_reapprove_are_consistent(pg) -> None:
    conn, _created = pg
    doc_id = _seed(pg)
    approve_id = _proposal(conn, doc_id, "Approved diagram")
    reject_id = _proposal(conn, doc_id, "Rejected diagram")
    schema = build_schema()

    async def check() -> None:
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            pending = await schema.execute(_LIST, variable_values={"d": doc_id}, context_value=ctx)
            assert pending.errors is None, pending.errors
            assert {row["id"] for row in pending.data["visualReview"]} == {
                approve_id,
                reject_id,
            }

            decided = await schema.execute(
                _DECIDE,
                variable_values={"a": [approve_id], "r": [reject_id]},
                context_value=ctx,
            )
            assert decided.errors is None, decided.errors
            assert decided.data["decideVisualCaptions"] == 2

            committed = await schema.execute(
                _CAPTIONS, variable_values={"d": doc_id}, context_value=ctx
            )
            assert committed.errors is None, committed.errors
            assert committed.data["visualCaptions"] == [
                {"caption": "Approved diagram", "groundingScore": 0.72}
            ]

            again = await schema.execute(
                _DECIDE,
                variable_values={"a": [approve_id], "r": []},
                context_value=ctx,
            )
            assert again.errors is None, again.errors
            assert again.data["decideVisualCaptions"] == 0
        finally:
            await aconn.close()

    asyncio.run(check())
    rows = conn.execute(
        "SELECT id, status, reviewer FROM visual_review WHERE id IN (%s, %s) ORDER BY id",
        (approve_id, reject_id),
    ).fetchall()
    assert {row["status"] for row in rows} == {"approved", "rejected"}
    assert all(str(row["reviewer"]) == WRITE_PRINCIPAL.principal_id for row in rows)


def test_visual_review_isolation_blocks_cross_principal_list_and_decide(pg) -> None:
    conn, created = pg
    alice = str(uuid.uuid4())
    bob = str(uuid.uuid4())
    _itdb.ensure_member_principal(conn, alice, f"visual-alice-{alice[:8]}")
    _itdb.ensure_member_principal(conn, bob, f"visual-bob-{bob[:8]}")
    doc_id = _itdb.make_doc(conn, "An owner-scoped visual review document.")
    created.append(doc_id)
    conn.execute("UPDATE documents SET owner_principal_id = %s WHERE id = %s", (alice, doc_id))
    review_id = _proposal(conn, doc_id, "Owner-only caption")
    schema = build_schema()

    async def as_principal(principal_id: str, *, decide: bool = False):
        aconn = await _aconn_as(principal_id)
        principal = Principal(
            subject=principal_id,
            principal_id=principal_id,
            scopes=frozenset({"read", "write"}),
        )
        try:
            ctx = RequestContext(conn=aconn, principal=principal, loaders=Loaders(aconn))
            document = _DECIDE if decide else _LIST
            variables = {"a": [review_id], "r": []} if decide else {"d": doc_id}
            return await schema.execute(document, variable_values=variables, context_value=ctx)
        finally:
            await aconn.close()

    alice_list = asyncio.run(as_principal(alice))
    bob_list = asyncio.run(as_principal(bob))
    assert alice_list.errors is None, alice_list.errors
    assert [row["id"] for row in alice_list.data["visualReview"]] == [review_id]
    assert bob_list.errors is None, bob_list.errors
    assert bob_list.data["visualReview"] == []

    bob_decide = asyncio.run(as_principal(bob, decide=True))
    assert bob_decide.errors is None, bob_decide.errors
    assert bob_decide.data["decideVisualCaptions"] == 0
    assert (
        conn.execute("SELECT status FROM visual_review WHERE id=%s", (review_id,)).fetchone()[
            "status"
        ]
        == "pending"
    )
