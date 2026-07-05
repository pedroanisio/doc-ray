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
