"""GraphQL canonicalization and review read-surface tests."""

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


def test_graphql_exposes_canonical_registry_and_review_residue(pg):
    """Canonical rows, forms, unresolved mentions, and reviews are queryable."""
    import _itdb

    conn, created = pg
    doc_id = _itdb.make_doc(conn, "Elena visited Berlin.")
    created.append(doc_id)
    qid = f"Q-test-{new_ulid_str()[-8:]}"
    canonical = conn.execute(
        "INSERT INTO canonical (preferred, entity_type, wikidata_id, description, lang_primary) "
        "VALUES ('Berlin', 'PLACE', %s, 'Capital of Germany', 'en') RETURNING id",
        (qid,),
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO form_index (form_norm, form_surface, canonical_id, lang, form_type, "
        "confidence, occurrences, source_types) VALUES ('berlin', 'Berlin', %s, 'en', "
        "'name', 1.0, 2, ARRAY['wikidata'])",
        (canonical,),
    )
    ann = _itdb.make_annotation(conn, doc_id, "ner", "test")
    mention_id = new_ulid_str()
    conn.execute(
        "INSERT INTO entity_mention (id, annotation_id, span_id, entity_type) VALUES (%s,%s,%s,'PERSON')",
        (mention_id, ann, _itdb.span_of(conn, doc_id, 0, 5)),
    )
    review_id = new_ulid_str()
    conn.execute(
        "INSERT INTO resolution_review "
        "(id, mention_id, candidates, proposed_canonical_id, confidence, status, reason) "
        "VALUES (%s, %s, jsonb_build_array(jsonb_build_object('canonical_id', %s)), "
        "%s, 0.42, 'pending', 'stub abstain')",
        (review_id, mention_id, canonical, canonical),
    )
    schema = build_schema()

    async def check() -> None:
        aconn = await _aconn()
        try:
            ctx = RequestContext(conn=aconn, principal=ANONYMOUS, loaders=Loaders(aconn))
            result = await schema.execute(
                """
                query($doc: ID!, $canonical: Int!, $q: String!) {
                  canonicalSearch(q: $q) {
                    id preferred entityType wikidataId forms { formSurface occurrences } cursor
                  }
                  canonical(id: $canonical) { preferred forms { formNorm formType } }
                  unresolvedMentions(documentId: $doc) { mentionId surface entityType cursor }
                  resolutionReview(documentId: $doc) {
                    id mentionId status reason proposedCanonical { preferred } cursor
                  }
                }
                """,
                variable_values={"doc": str(doc_id), "canonical": canonical, "q": qid},
                context_value=ctx,
            )
            assert result.errors is None, result.errors
            assert result.data["canonicalSearch"][0]["forms"][0]["formSurface"] == "Berlin"
            assert result.data["canonical"]["forms"][0]["formNorm"] == "berlin"
            assert result.data["unresolvedMentions"][0]["surface"] == "Elena"
            assert result.data["resolutionReview"][0]["proposedCanonical"]["preferred"] == "Berlin"
        finally:
            await aconn.close()

    asyncio.run(check())
