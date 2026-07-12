"""GraphQL lexeme visibility must not leak hidden-document vocabulary."""

from __future__ import annotations

import asyncio

from doc_ray.adapters import persistence as db
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.schema import build_schema
from doc_ray.entrypoints.api.security import ANONYMOUS, Principal
from doc_ray.tests._mutations_common import _aconn_as

ADMIN = Principal(
    subject="test-lexeme-reader",
    principal_id="00000000-0000-0000-0000-000000000127",
    scopes=frozenset({"read"}),
)


def _seed_private_lexeme(pg) -> str:
    import _itdb

    conn, created = pg
    _itdb.ensure_admin_principal(conn, ADMIN.principal_id or "", "test-lexeme-reader")
    doc_id = _itdb.make_doc(conn, "Zyzzogeton appears once.")
    created.append(doc_id)
    annotation_id = _itdb.make_annotation(conn, doc_id, "lexeme", "test")
    span_id = db.ensure_spans(conn, doc_id, [(0, 10)])[(0, 10)]
    lexeme_uid, _created = db.get_or_create_lexeme(conn, {}, "zyzzogeton", "NOUN", "en")
    conn.execute(
        "INSERT INTO lexeme_occurrences (annotation_id, span_id, lexeme_uid) "
        "VALUES (%s, %s, %s)",
        (annotation_id, span_id, lexeme_uid),
    )
    return lexeme_uid


def test_anonymous_graphql_lexemes_do_not_reveal_hidden_document_vocabulary(pg) -> None:
    lexeme_uid = _seed_private_lexeme(pg)
    schema = build_schema()

    async def check() -> None:
        anon_conn = await _aconn_as(None)
        admin_conn = await _aconn_as(ADMIN.principal_id)
        try:
            anon_ctx = RequestContext(
                conn=anon_conn, principal=ANONYMOUS, loaders=Loaders(anon_conn)
            )
            admin_ctx = RequestContext(
                conn=admin_conn, principal=ADMIN, loaders=Loaders(admin_conn)
            )

            anonymous_list = await schema.execute(
                'query { lexemes(q: "zyzzogeton", limit: 10) { uid lemma uses } }',
                context_value=anon_ctx,
            )
            assert anonymous_list.errors is None, anonymous_list.errors
            assert anonymous_list.data == {"lexemes": []}

            anonymous_one = await schema.execute(
                "query($uid: ID!) { lexeme(uid: $uid) { uid lemma } }",
                variable_values={"uid": lexeme_uid},
                context_value=anon_ctx,
            )
            assert anonymous_one.errors is None, anonymous_one.errors
            assert anonymous_one.data == {"lexeme": None}

            admin_list = await schema.execute(
                'query { lexemes(q: "zyzzogeton", limit: 10) { uid lemma uses } }',
                context_value=admin_ctx,
            )
            assert admin_list.errors is None, admin_list.errors
            assert admin_list.data == {
                "lexemes": [{"uid": lexeme_uid, "lemma": "zyzzogeton", "uses": 1}]
            }
        finally:
            await anon_conn.close()
            await admin_conn.close()

    asyncio.run(check())
