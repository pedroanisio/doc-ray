"""GraphQL API tests: schema-level execution against the read adapter.

The depth-limit test runs anywhere (it's a validation rule, no DB needed). The
query tests seed with the sync helpers and execute the schema against an async
connection, skipping cleanly when Postgres is unreachable.
"""

from __future__ import annotations

import asyncio
from typing import Any

import psycopg
from psycopg.rows import dict_row

from doc_ray.domain.ulid import new_ulid_str
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.schema import build_schema
from doc_ray.entrypoints.api.security import ANONYMOUS

_TEXT = "Elena founded a startup in Berlin. It grew fast."


def _seed_text(pg, text: str) -> tuple[Any, str]:
    """Seed a throwaway document with *text*; return (sync_conn, doc_id)."""
    import _itdb

    conn, created = pg
    doc_id = _itdb.make_doc(conn, text)
    created.append(doc_id)
    return conn, doc_id


def _ctx(aconn) -> RequestContext:
    return RequestContext(conn=aconn, principal=ANONYMOUS, loaders=Loaders(aconn))


async def _aconn() -> psycopg.AsyncConnection:
    import _itdb

    return await psycopg.AsyncConnection.connect(
        _itdb.DB_URL, row_factory=dict_row, autocommit=True
    )


def _seed(pg) -> str:
    import _itdb

    conn, created = pg
    doc_id = _itdb.make_doc(conn, _TEXT)
    created.append(doc_id)
    return doc_id


def test_depth_limit_rejects_deep_queries():
    # Validation-time rule: no DB or context needed — the query never executes.
    schema = build_schema(max_depth=1)

    async def check():
        # document { sentences { ordinal } } nests two object levels (depth 2 > 1).
        result = await schema.execute(
            '{ document(id: "x") { sentences { ordinal } } }', context_value=None
        )
        assert result.errors, "expected the depth limiter to reject a depth-2 query"
        assert "depth" in str(result.errors[0]).lower()

    asyncio.run(check())


def test_introspection_disabled_by_default():
    # Validation-time rule, same as the depth limiter above: no DB needed.
    schema = build_schema()

    async def check():
        result = await schema.execute("{ __schema { queryType { name } } }", context_value=None)
        assert result.errors, "expected introspection to be disabled by default (BL-108)"
        assert "introspection" in str(result.errors[0]).lower()

    asyncio.run(check())


def test_introspection_allowed_with_debug_enabled():
    # The explicit opt-in (mirrors DOCRAY_API_DEBUG) still allows schema exploration.
    schema = build_schema(debug=True)

    async def check():
        result = await schema.execute("{ __schema { queryType { name } } }", context_value=None)
        assert result.errors is None, result.errors
        assert result.data["__schema"]["queryType"]["name"] == "Query"

    asyncio.run(check())


def test_documents_and_document(pg):
    doc_id = _seed(pg)
    schema = build_schema()

    async def check():
        aconn = await _aconn()
        try:
            ctx = RequestContext(conn=aconn, principal=ANONYMOUS, loaders=Loaders(aconn))
            listing = await schema.execute(
                "{ documents(limit: 50) { id title sentenceCount authors } }",
                context_value=ctx,
            )
            assert listing.errors is None, listing.errors
            mine = [d for d in listing.data["documents"] if d["id"] == str(doc_id)]
            assert mine and mine[0]["sentenceCount"] == 2
            assert isinstance(mine[0]["authors"], list)

            detail = await schema.execute(
                "query ($id: ID!) { document(id: $id) "
                "{ title status sentences(limit: 5) { ordinal text } } }",
                variable_values={"id": str(doc_id)},
                context_value=ctx,
            )
            assert detail.errors is None, detail.errors
            assert detail.data["document"]["title"] == "itest"
            assert len(detail.data["document"]["sentences"]) == 2

            missing = await schema.execute(
                '{ document(id: "00000000-0000-0000-0000-000000000000") { title } }',
                context_value=ctx,
            )
            assert missing.errors is None, missing.errors
            assert missing.data["document"] is None
        finally:
            await aconn.close()

    asyncio.run(check())


def test_search_and_lexeme(pg):
    doc_id = _seed(pg)
    schema = build_schema()

    async def check():
        aconn = await _aconn()
        try:
            ctx = RequestContext(conn=aconn, principal=ANONYMOUS, loaders=Loaders(aconn))
            search = await schema.execute(
                '{ searchSentences(term: "startup") { documentId hit } }',
                context_value=ctx,
            )
            assert search.errors is None, search.errors
            assert any(
                h["documentId"] == str(doc_id) and h["hit"] == "startup"
                for h in search.data["searchSentences"]
            )

            lexeme = await schema.execute(
                '{ lexeme(uid: "lx_none.x_deadbeef") { lemma } }', context_value=ctx
            )
            assert lexeme.errors is None, lexeme.errors
            assert lexeme.data["lexeme"] is None
        finally:
            await aconn.close()

    asyncio.run(check())


def test_document_bibliographic_and_token_count(pg):
    """Document exposes publisher/publishedOn/subtitle/license + a derived tokenCount."""
    import _itdb

    conn, doc_id = _seed_text(pg, _TEXT)
    conn.execute(
        "UPDATE documents SET publisher=%s, published_on=%s, subtitle=%s, license=%s WHERE id=%s",
        ("Secker & Warburg", "1949-06-08", "A Novel", "Public Domain", doc_id),
    )
    ann = _itdb.make_annotation(conn, doc_id, "spacy", "spacy/en")
    toks = _itdb.make_tokens(conn, doc_id, ann, _TEXT)
    schema = build_schema()

    async def check():
        aconn = await _aconn()
        try:
            r = await schema.execute(
                "query($id: ID!){ document(id:$id){ publisher publishedOn subtitle "
                "license tokenCount } }",
                variable_values={"id": str(doc_id)},
                context_value=_ctx(aconn),
            )
            assert r.errors is None, r.errors
            d = r.data["document"]
            assert d["publisher"] == "Secker & Warburg"
            assert d["publishedOn"] == "1949-06-08"
            assert d["subtitle"] == "A Novel"
            assert d["license"] == "Public Domain"
            assert d["tokenCount"] == len(toks)
        finally:
            await aconn.close()

    asyncio.run(check())


def test_search_sentences_id_cursor_and_document_filter(pg):
    """searchSentences carries a stable id, filters by documentId, and pages by cursor."""
    conn, doc_id = _seed_text(pg, "Winston walked. Winston paused. Winston left.")
    schema = build_schema()

    async def check():
        aconn = await _aconn()
        try:
            ctx = _ctx(aconn)
            sents = await schema.execute(
                "query($id:ID!){ document(id:$id){ sentences{ id ordinal } } }",
                variable_values={"id": str(doc_id)},
                context_value=ctx,
            )
            by_ord = {s["ordinal"]: s["id"] for s in sents.data["document"]["sentences"]}

            allhits = await schema.execute(
                'query($id:ID!){ searchSentences(term:"Winston", documentId:$id) '
                "{ id ordinal documentId cursor } }",
                variable_values={"id": str(doc_id)},
                context_value=ctx,
            )
            assert allhits.errors is None, allhits.errors
            hits = allhits.data["searchSentences"]
            assert len(hits) == 3  # documentId filter isolates from the corpus (Winston is common)
            assert all(h["documentId"] == str(doc_id) for h in hits)
            assert hits[0]["id"] == by_ord[hits[0]["ordinal"]]  # stable sentence id, no round-trip

            page = await schema.execute(
                'query($id:ID!,$after:String!){ searchSentences(term:"Winston", documentId:$id, '
                "after:$after, limit:1){ ordinal } }",
                variable_values={"id": str(doc_id), "after": hits[0]["cursor"]},
                context_value=ctx,
            )
            assert page.errors is None, page.errors
            assert [h["ordinal"] for h in page.data["searchSentences"]] == [hits[1]["ordinal"]]
        finally:
            await aconn.close()

    asyncio.run(check())


def test_lexemes_and_occurrences_document_scope(pg):
    """lexemes(documentId:) and Lexeme.occurrences(documentId:) scope counts to one doc."""
    import _itdb

    conn, created = pg
    d1 = _itdb.make_doc(conn, "alpha beta gamma.")
    d2 = _itdb.make_doc(conn, "alpha delta.")
    created += [d1, d2]
    nonce = new_ulid_str()[-8:].lower()
    lemma, uid = f"itlex{nonce}", f"lx_en.noun_{nonce}"
    conn.execute(
        "INSERT INTO lexemes (uid, lang, pos, lemma) VALUES (%s,'en','NOUN',%s)", (uid, lemma)
    )
    a1 = _itdb.make_annotation(conn, d1, "lexeme", "spacy/en")
    a2 = _itdb.make_annotation(conn, d2, "lexeme", "spacy/en")
    for s, e in [(0, 5), (6, 10)]:  # two occurrences in d1 ("alpha", "beta")
        conn.execute(
            "INSERT INTO lexeme_occurrences (annotation_id, span_id, lexeme_uid) VALUES (%s,%s,%s)",
            (a1, _itdb.span_of(conn, d1, s, e), uid),
        )
    conn.execute(  # one occurrence in d2
        "INSERT INTO lexeme_occurrences (annotation_id, span_id, lexeme_uid) VALUES (%s,%s,%s)",
        (a2, _itdb.span_of(conn, d2, 0, 5), uid),
    )
    schema = build_schema()

    async def check():
        aconn = await _aconn()
        try:
            ctx = _ctx(aconn)
            scoped = await schema.execute(
                "query($d:ID!,$q:String!){ lexemes(documentId:$d, q:$q){ uid uses } }",
                variable_values={"d": str(d1), "q": lemma},
                context_value=ctx,
            )
            assert scoped.errors is None, scoped.errors
            assert scoped.data["lexemes"][0]["uses"] == 2  # only d1's occurrences

            corpus = await schema.execute(
                "query($q:String!){ lexemes(q:$q){ uses } }",
                variable_values={"q": lemma},
                context_value=ctx,
            )
            assert corpus.data["lexemes"][0]["uses"] == 3  # d1 + d2

            occ = await schema.execute(
                "query($u:ID!,$d:ID!){ lexeme(uid:$u){ occurrences(documentId:$d){ documentId } } }",
                variable_values={"u": uid, "d": str(d1)},
                context_value=ctx,
            )
            assert occ.errors is None, occ.errors
            occs = occ.data["lexeme"]["occurrences"]
            assert len(occs) == 2 and all(o["documentId"] == str(d1) for o in occs)
        finally:
            await aconn.close()

    asyncio.run(check())


def test_entities_query_document_field_and_frequency(pg):
    """NER is queryable: entities(...) top-level, Document.entities, and entityFrequency."""
    import _itdb

    txt = "Winston met Winston in London."
    conn, doc_id = _seed_text(pg, txt)
    ann = _itdb.make_annotation(conn, doc_id, "ner", "spacy/en")

    def ent(start: int, sub: str, etype: str) -> None:
        conn.execute(
            "INSERT INTO entity_mention (id, annotation_id, span_id, entity_type, surface) "
            "VALUES (%s,%s,%s,%s,%s)",
            (
                new_ulid_str(),
                ann,
                _itdb.span_of(conn, doc_id, start, start + len(sub)),
                etype,
                sub,
            ),
        )

    w1 = txt.index("Winston")
    ent(w1, "Winston", "PERSON")
    ent(txt.index("Winston", w1 + 1), "Winston", "PERSON")
    ent(txt.index("London"), "London", "GPE")
    schema = build_schema()

    async def check():
        aconn = await _aconn()
        try:
            ctx = _ctx(aconn)
            allents = await schema.execute(
                "query($id:ID!){ entities(documentId:$id){ entityType text documentId } }",
                variable_values={"id": str(doc_id)},
                context_value=ctx,
            )
            assert allents.errors is None, allents.errors
            got = {(e["entityType"], e["text"]) for e in allents.data["entities"]}
            assert got == {("PERSON", "Winston"), ("GPE", "London")}
            assert all(e["documentId"] == str(doc_id) for e in allents.data["entities"])

            persons = await schema.execute(
                'query($id:ID!){ entities(documentId:$id, entityType:"PERSON"){ text } }',
                variable_values={"id": str(doc_id)},
                context_value=ctx,
            )
            assert [e["text"] for e in persons.data["entities"]] == ["Winston", "Winston"]

            viafield = await schema.execute(
                "query($id:ID!){ document(id:$id){ entities{ entityType } } }",
                variable_values={"id": str(doc_id)},
                context_value=ctx,
            )
            assert len(viafield.data["document"]["entities"]) == 3

            freq = await schema.execute(
                'query($id:ID!){ entityFrequency(documentId:$id, entityType:"PERSON")'
                "{ text entityType count } }",
                variable_values={"id": str(doc_id)},
                context_value=ctx,
            )
            assert freq.errors is None, freq.errors
            assert freq.data["entityFrequency"] == [
                {"text": "Winston", "entityType": "PERSON", "count": 2}
            ]
        finally:
            await aconn.close()

    asyncio.run(check())


# ---------------------------------------------------------------------------
# BL-110 — a malformed id-shaped argument must read as "not found" / an empty
# page, never forward Postgres's raw format-violation exception text.
# ---------------------------------------------------------------------------


def test_document_rejects_malformed_id_without_leaking_postgres_error(pg):
    schema = build_schema()

    async def check():
        aconn = await _aconn()
        try:
            ctx = _ctx(aconn)
            r = await schema.execute('{ document(id: "not-a-uuid") { title } }', context_value=ctx)
            assert r.errors is None, r.errors
            assert r.data["document"] is None
        finally:
            await aconn.close()

    asyncio.run(check())


def test_job_rejects_malformed_id_without_leaking_postgres_error(pg):
    schema = build_schema()

    async def check():
        aconn = await _aconn()
        try:
            ctx = _ctx(aconn)
            r = await schema.execute('{ job(id: "not-a-ulid") { status } }', context_value=ctx)
            assert r.errors is None, r.errors
            assert r.data["job"] is None
        finally:
            await aconn.close()

    asyncio.run(check())


def test_optional_document_id_filters_reject_malformed_id_as_empty_page(pg):
    """search_sentences/lexemes/entities/entityFrequency/resolutionReview."""
    schema = build_schema()
    queries = [
        '{ searchSentences(term: "x", documentId: "not-a-uuid") { id } }',
        '{ lexemes(documentId: "not-a-uuid") { uid } }',
        '{ entities(documentId: "not-a-uuid") { id } }',
        '{ entityFrequency(documentId: "not-a-uuid") { text } }',
        '{ resolutionReview(documentId: "not-a-uuid") { id } }',
    ]

    async def check():
        aconn = await _aconn()
        try:
            ctx = _ctx(aconn)
            for q in queries:
                r = await schema.execute(q, context_value=ctx)
                assert r.errors is None, (q, r.errors)
                (field,) = r.data.values()
                assert field == [], (q, field)
        finally:
            await aconn.close()

    asyncio.run(check())


def test_required_document_id_list_resolvers_reject_malformed_id_as_empty_page(pg):
    """seedSuggestions/unresolvedMentions take a required documentId argument."""
    schema = build_schema()
    queries = [
        '{ seedSuggestions(documentId: "not-a-uuid") { id } }',
        '{ unresolvedMentions(documentId: "not-a-uuid") { mentionId } }',
    ]

    async def check():
        aconn = await _aconn()
        try:
            ctx = _ctx(aconn)
            for q in queries:
                r = await schema.execute(q, context_value=ctx)
                assert r.errors is None, (q, r.errors)
                (field,) = r.data.values()
                assert field == [], (q, field)
        finally:
            await aconn.close()

    asyncio.run(check())
