"""Tests for deterministic semantic enrichment layers."""

from __future__ import annotations

import _itdb

from doc_ray.application import semantic_enrichment as se


def test_semantic_enrichment_populates_schema_only_layers(pg):
    conn, created = pg
    text = "Ada met Bob in 2026 because the result was good."
    doc_id = _itdb.make_doc(conn, text)
    created.append(doc_id)
    annotation_id = _itdb.make_annotation(conn, doc_id, "spacy", "pytest/tokens")
    _itdb.make_tokens(
        conn,
        doc_id,
        annotation_id,
        text,
        upos={"met": "VERB", "was": "AUX"},
    )

    summary = se.annotate_document(conn, doc_id)

    assert summary == {
        "discourse_relations": 1,
        "timexes": 1,
        "events": 1,
        "sentiments": 1,
        "embeddings": 1,
    }
    annotation = conn.execute(
        "SELECT id FROM annotations WHERE document_id=%s AND layer=%s AND tool=%s",
        (doc_id, se.LAYER_NAME, se.TOOL_NAME),
    ).fetchone()
    assert annotation is not None
    ann_id = annotation["id"]
    assert (
        conn.execute(
            "SELECT count(*) AS n FROM discourse_relation WHERE annotation_id=%s", (ann_id,)
        ).fetchone()["n"]
        == 1
    )
    assert (
        conn.execute(
            "SELECT count(*) AS n FROM timex WHERE annotation_id=%s", (ann_id,)
        ).fetchone()["n"]
        == 1
    )
    assert (
        conn.execute(
            "SELECT count(*) AS n FROM event WHERE annotation_id=%s", (ann_id,)
        ).fetchone()["n"]
        == 1
    )
    sentiment = conn.execute(
        "SELECT polarity, score FROM sentiment WHERE annotation_id=%s", (ann_id,)
    ).fetchone()
    assert sentiment["polarity"] == "POSITIVE"
    assert sentiment["score"] > 0
    embedding = conn.execute(
        """
        SELECT vector_dims(e.vector) AS dims
          FROM embedding e
          JOIN spans sp ON sp.id = e.span_id
         WHERE sp.document_id = %s AND e.model = %s
        """,
        (doc_id, se.EMBEDDING_MODEL),
    ).fetchone()
    assert embedding["dims"] == se.EMBEDDING_DIMENSIONS


def test_semantic_enrichment_is_idempotent_for_document(pg):
    conn, created = pg
    text = "The result was bad. Ada met Bob in 2026."
    doc_id = _itdb.make_doc(conn, text)
    created.append(doc_id)
    annotation_id = _itdb.make_annotation(conn, doc_id, "spacy", "pytest/tokens")
    _itdb.make_tokens(conn, doc_id, annotation_id, text, upos={"met": "VERB", "was": "AUX"})

    first = se.annotate_document(conn, doc_id)
    second = se.annotate_document(conn, doc_id)

    assert second == first
    assert (
        conn.execute(
            "SELECT count(*) AS n FROM annotations WHERE document_id=%s AND layer=%s AND tool=%s",
            (doc_id, se.LAYER_NAME, se.TOOL_NAME),
        ).fetchone()["n"]
        == 1
    )
    assert (
        conn.execute(
            """
            SELECT count(*) AS n
              FROM embedding e
              JOIN spans sp ON sp.id = e.span_id
             WHERE sp.document_id = %s AND e.model = %s
            """,
            (doc_id, se.EMBEDDING_MODEL),
        ).fetchone()["n"]
        == first["embeddings"]
    )
