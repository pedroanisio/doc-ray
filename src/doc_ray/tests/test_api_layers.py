"""GraphQL reads for persisted NLP annotation layers."""

from __future__ import annotations

import asyncio

import psycopg
from psycopg.rows import dict_row

from doc_ray.domain.ulid import new_ulid_str
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.schema import build_schema
from doc_ray.entrypoints.api.security import ANONYMOUS


async def _aconn() -> psycopg.AsyncConnection:
    import _itdb

    return await psycopg.AsyncConnection.connect(
        _itdb.DB_URL, row_factory=dict_row, autocommit=True
    )


def test_graphql_exposes_persisted_nlp_layers(pg):
    """Annotation runs, tokens, syntax, coref, and entity links are queryable."""
    import _itdb

    text = "Elena founded Berlin."
    conn, created = pg
    doc_id = _itdb.make_doc(conn, text)
    created.append(doc_id)
    ann = _itdb.make_annotation(conn, doc_id, "spacy", "spacy/en", "3.8")
    tokens = _itdb.make_tokens(conn, doc_id, ann, text, {"Elena": "PROPN", "founded": "VERB"})
    conn.execute(
        "INSERT INTO token_feature (token_id, feature, value) VALUES (%s, 'Number', 'Sing')",
        (tokens[0]["id"],),
    )
    conn.execute(
        "INSERT INTO dependency (annotation_id, dependent_token_id, head_token_id, deprel) "
        "VALUES (%s, %s, %s, 'nsubj')",
        (ann, tokens[0]["id"], tokens[1]["id"]),
    )
    conn.execute(
        "INSERT INTO chunk (id, annotation_id, span_id, chunk_type, surface) "
        "VALUES (%s, %s, %s, 'NP', %s)",
        (new_ulid_str(), ann, tokens[0]["span_id"], tokens[0]["surface"]),
    )
    mention_id = new_ulid_str()
    conn.execute(
        "INSERT INTO entity_mention (id, annotation_id, span_id, entity_type, surface) "
        "VALUES (%s,%s,%s,'PERSON',%s)",
        (mention_id, ann, tokens[0]["span_id"], tokens[0]["surface"]),
    )
    conn.execute(
        "INSERT INTO entity_link (mention_id, scheme, kb_id, confidence) VALUES (%s,'wikidata','Q1',0.9)",
        (mention_id,),
    )
    chain_id = new_ulid_str()
    conn.execute(
        "INSERT INTO coref_chain (id, annotation_id, document_id) VALUES (%s,%s,%s)",
        (chain_id, ann, doc_id),
    )
    conn.execute(
        "INSERT INTO coref_mention (chain_id, span_id) VALUES (%s,%s)",
        (chain_id, tokens[0]["span_id"]),
    )
    schema = build_schema()

    async def check() -> None:
        aconn = await _aconn()
        try:
            ctx = RequestContext(conn=aconn, principal=ANONYMOUS, loaders=Loaders(aconn))
            result = await schema.execute(
                """
                query($id: ID!) {
                  document(id: $id) {
                    annotationRuns { id layer tool toolVersion cursor }
                    tokens { ordinal surface upos features { feature value } cursor }
                    dependencies { dependentTokenId headTokenId deprel cursor }
                    chunks { chunkType surface cursor }
                    corefChains { id mentions { surface } cursor }
                    entityLinks { mentionId scheme kbId confidence surface cursor }
                  }
                }
                """,
                variable_values={"id": str(doc_id)},
                context_value=ctx,
            )
            assert result.errors is None, result.errors
            doc = result.data["document"]
            assert doc["annotationRuns"][0]["toolVersion"] == "3.8"
            assert doc["tokens"][0]["features"] == [{"feature": "Number", "value": "Sing"}]
            assert doc["dependencies"][0]["deprel"] == "nsubj"
            assert doc["chunks"][0]["surface"] == "Elena"
            assert doc["corefChains"][0]["mentions"] == [{"surface": "Elena"}]
            assert doc["entityLinks"][0]["kbId"] == "Q1"
        finally:
            await aconn.close()

    asyncio.run(check())


# --- Layers 9 and 14 end to end (regression) ---------------------------------
#
# Both layers were "schema-only" — tables existed, nothing wrote them, nothing read
# them. These assert the whole path: annotate derives them from the parse, the read
# adapters return them, and the GraphQL fields expose them.


def test_constituents_and_srl_frames_are_written_and_readable(pg):
    import asyncio

    import _itdb

    from doc_ray.adapters import persistence_syntax_read as syntax_read
    from doc_ray.adapters.annotation_spacy import SpacyAnnotator

    conn, created = pg
    doc_id = _itdb.make_doc(conn, "Elena founded a startup in Berlin.")
    created.append(doc_id)
    SpacyAnnotator().annotate(conn, doc_id, lang_default="en")

    async def check() -> None:
        # The read adapters are async (they serve the GraphQL resolvers), so they
        # need the async connection, not the fixture's sync one.
        aconn = await _aconn()
        try:
            nodes = await syntax_read.list_constituents(aconn, str(doc_id))
            frames = await syntax_read.list_srl_frames(aconn, str(doc_id))
        finally:
            await aconn.close()

        labels = {n["label"] for n in nodes}
        assert "S" in labels, f"no sentence root projected: {labels}"
        assert {"NP", "VP"} <= labels, f"expected NP and VP, got {labels}"
        # Exactly one root, and every surface reconstructs from the document text.
        assert len([n for n in nodes if n["parent_id"] is None]) == 1
        assert all(n["surface"] for n in nodes)

        assert frames, "no SRL frame written for a sentence with a transitive verb"
        found = next(f for f in frames if f["frame"] == "found")
        roles = {a["role"] for a in found["arguments"]}
        assert "ARG0" in roles and "ARG1" in roles, f"missing core roles: {roles}"
        assert ".01" not in found["frame"]  # no fabricated PropBank roleset

    asyncio.run(check())


def test_reannotation_replaces_syntax_layers_rather_than_accumulating(pg):
    import asyncio

    import _itdb

    from doc_ray.adapters import persistence_syntax_read as syntax_read
    from doc_ray.adapters.annotation_spacy import SpacyAnnotator

    conn, created = pg
    doc_id = _itdb.make_doc(conn, "Investors trusted her vision.")
    created.append(doc_id)

    async def counts() -> tuple[int, int]:
        aconn = await _aconn()
        try:
            nodes = await syntax_read.list_constituents(aconn, str(doc_id))
            frames = await syntax_read.list_srl_frames(aconn, str(doc_id))
            return len(nodes), len(frames)
        finally:
            await aconn.close()

    SpacyAnnotator().annotate(conn, doc_id, lang_default="en")
    first = asyncio.run(counts())
    SpacyAnnotator().annotate(conn, doc_id, lang_default="en")
    second = asyncio.run(counts())

    assert first[0] > 0 and first[1] > 0
    assert second == first, "re-annotation accumulated instead of replacing"


def test_graphql_exposes_both_layers(pg):
    import asyncio

    import _itdb

    from doc_ray.adapters.annotation_spacy import SpacyAnnotator

    conn, created = pg
    doc_id = _itdb.make_doc(conn, "Elena founded a startup in Berlin.")
    created.append(doc_id)
    SpacyAnnotator().annotate(conn, doc_id, lang_default="en")
    schema = build_schema()

    async def check() -> None:
        aconn = await _aconn()
        try:
            ctx = RequestContext(conn=aconn, principal=ANONYMOUS, loaders=Loaders(aconn))
            result = await schema.execute(
                """
                query($id: ID!) {
                  document(id: $id) {
                    constituents(limit: 50) { label surface parentId charStart charEnd cursor }
                    srlFrames(limit: 10) { frame surface arguments { role surface } cursor }
                  }
                }
                """,
                variable_values={"id": str(doc_id)},
                context_value=ctx,
            )
            assert result.errors is None, result.errors
            doc = result.data["document"]
            assert any(n["label"] == "S" for n in doc["constituents"])
            assert any(n["parentId"] is None for n in doc["constituents"])
            assert any(f["frame"] == "found" for f in doc["srlFrames"])
            assert any(a["role"] == "ARG0" for f in doc["srlFrames"] for a in f["arguments"])
        finally:
            await aconn.close()

    asyncio.run(check())
