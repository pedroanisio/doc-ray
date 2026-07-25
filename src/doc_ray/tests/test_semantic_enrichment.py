"""Tests for deterministic semantic enrichment layers."""

from __future__ import annotations

import _itdb

from doc_ray.application import semantic_enrichment as se


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    """Records executed SQL; serves canned full_text and token rows (no DB)."""

    def __init__(self, full_text, token_rows):
        self._full_text = full_text
        self._token_rows = token_rows
        self.statements = []

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        self.statements.append(s)
        if s.startswith("SELECT full_text FROM document_content"):
            return _FakeCursor([{"full_text": self._full_text}])
        if "FROM tokens" in s:
            return _FakeCursor([dict(r) for r in self._token_rows])
        raise AssertionError(f"unexpected query: {s}")


def test_tokens_slices_surfaces_in_python_not_sql():
    """Per-row substring(full_text ...) detoasts the whole document text once
    per token (measured 76.5s for a 98k-token document on the live corpus);
    surfaces must come from one full_text fetch sliced in Python instead."""
    text = "Ada met Zoë."
    rows = [
        {"id": "t1", "upos": "PROPN", "span_id": "s1", "char_start": 0, "char_end": 3},
        {"id": "t2", "upos": "VERB", "span_id": "s2", "char_start": 4, "char_end": 7},
        {"id": "t3", "upos": "PROPN", "span_id": "s3", "char_start": 8, "char_end": 11},
    ]
    conn = _FakeConn(text, rows)

    result = se._tokens(conn, "doc-1")

    assert not any("substring(" in s.lower() for s in conn.statements), (
        "token surfaces must be sliced in Python from a single full_text "
        "fetch, not computed per-row in SQL"
    )
    assert [r["surface"] for r in result] == ["Ada", "met", "Zoë"]
    assert [r["upos"] for r in result] == ["PROPN", "VERB", "PROPN"]
    assert [r["span_id"] for r in result] == ["s1", "s2", "s3"]


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
    # Regression (hard cutover): semantic_enrich no longer writes embeddings —
    # that responsibility moved to the learned `semantic_embed` writer.
    embedding_rows = conn.execute(
        """
        SELECT count(*) AS n
          FROM embedding e
          JOIN spans sp ON sp.id = e.span_id
         WHERE sp.document_id = %s
        """,
        (doc_id,),
    ).fetchone()
    assert embedding_rows["n"] == 0


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
