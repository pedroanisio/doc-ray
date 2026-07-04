"""Regression tests: the spaCy pass produces the COMPLETE, REAL NLP mapping.

Runs against the live Postgres (skips if unreachable) on an isolated throwaway
document, so it is deterministic regardless of concurrent corpus churn. Guards
against the exact regressions that bit us: empty annotations, fake pos='X', or
the lexeme layer (what the webapp reads) not being produced.
"""

from __future__ import annotations

import _itdb
import pytest

from doc_ray.adapters.annotation_spacy import SpacyAnnotator

# Rich enough for verbs, nouns, named entities, and a dependency root.
TEXT = (
    "Albert Einstein founded modern physics in Germany. "
    "He later published the general theory of relativity."
)


def _n(conn, sql, doc_id):
    return conn.execute(sql, (doc_id,)).fetchone()["n"]


@pytest.fixture
def mapped(pg):
    conn, reg = pg
    doc_id = _itdb.make_doc(conn, TEXT)
    reg.append(doc_id)
    result = SpacyAnnotator().annotate(conn, doc_id)
    return conn, doc_id, result


def test_lexeme_layer_has_real_pos(mapped):
    """The webapp's layer: lexeme_occurrences with genuine spaCy POS."""
    conn, doc_id, r = mapped
    assert r["occurrences"] > 0, "no lexeme occurrences produced"
    pos = {
        row["pos"]: row["n"]
        for row in conn.execute(
            "SELECT l.pos, count(*) n FROM lexeme_occurrences o "
            "JOIN annotations a ON a.id=o.annotation_id "
            "JOIN lexemes l ON l.uid=o.lexeme_uid "
            "WHERE a.document_id=%s AND a.layer='lexeme' GROUP BY l.pos",
            (doc_id,),
        ).fetchall()
    }
    assert pos.get("VERB", 0) > 0, pos  # real verbs
    assert pos.get("NOUN", 0) > 0, pos  # real nouns
    assert "X" not in pos  # NOT the fake rule-analyzer POS


def test_tokens_and_morphology(mapped):
    conn, doc_id, r = mapped
    assert (
        _n(
            conn,
            "SELECT count(*) n FROM tokens t JOIN annotations a ON a.id=t.annotation_id "
            "WHERE a.document_id=%s AND t.upos IS NOT NULL",
            doc_id,
        )
        > 0
    )
    assert (
        _n(
            conn,
            "SELECT count(*) n FROM token_feature tf JOIN tokens t ON t.id=tf.token_id "
            "JOIN annotations a ON a.id=t.annotation_id WHERE a.document_id=%s",
            doc_id,
        )
        > 0
    )


def test_dependency_tree(mapped):
    conn, doc_id, r = mapped
    assert (
        _n(
            conn,
            "SELECT count(*) n FROM dependency d JOIN annotations a ON a.id=d.annotation_id "
            "WHERE a.document_id=%s AND d.deprel='root'",
            doc_id,
        )
        >= 1
    )


def test_named_entities(mapped):
    conn, doc_id, r = mapped
    types = {
        row["entity_type"]
        for row in conn.execute(
            "SELECT DISTINCT em.entity_type FROM entity_mention em "
            "JOIN annotations a ON a.id=em.annotation_id WHERE a.document_id=%s",
            (doc_id,),
        ).fetchall()
    }
    assert types, "no named entities (expected PERSON/GPE)"


def test_noun_chunks(mapped):
    conn, doc_id, r = mapped
    assert (
        _n(
            conn,
            "SELECT count(*) n FROM chunk c JOIN annotations a ON a.id=c.annotation_id "
            "WHERE a.document_id=%s",
            doc_id,
        )
        > 0
    )


def test_one_pass_produces_all_layers(mapped):
    """A single annotate() call fills every layer atomically."""
    conn, doc_id, r = mapped
    layers = {
        row["layer"]
        for row in conn.execute(
            "SELECT DISTINCT layer FROM annotations WHERE document_id=%s", (doc_id,)
        ).fetchall()
    }
    assert {"spacy", "lexeme"} <= layers


def test_reannotation_is_idempotent(mapped):
    conn, doc_id, r = mapped
    SpacyAnnotator().annotate(conn, doc_id)  # run again -> replace, not duplicate
    counts = {
        row["layer"]: row["n"]
        for row in conn.execute(
            "SELECT layer, count(*) n FROM annotations WHERE document_id=%s "
            "AND layer IN ('spacy','lexeme') GROUP BY layer",
            (doc_id,),
        ).fetchall()
    }
    assert counts.get("spacy") == 1 and counts.get("lexeme") == 1, counts


def test_spacy_annotation_records_provenance_event(mapped):
    conn, doc_id, r = mapped
    tool, version = r["tool"].split("@", 1)
    event = conn.execute(
        """
        SELECT id, tool, tool_version, actor, note
        FROM document_provenance
        WHERE document_id=%s AND kind='annotated' AND tool=%s
        """,
        (doc_id, tool),
    ).fetchone()
    assert event is not None
    assert event["tool_version"] == version
    assert event["actor"] == "doc_ray.adapters.annotation_spacy.SpacyAnnotator"
    assert "spaCy multi-layer annotation" in event["note"]

    params = {
        row["key"]: row["value"]
        for row in conn.execute(
            "SELECT key, value FROM provenance_params WHERE event_id=%s",
            (event["id"],),
        ).fetchall()
    }
    assert params["language"] == r["lang"]
    assert params["replacement"] == "replace document spacy and lexeme layers for this tool"
    assert params["tokens"] == str(r["tokens"])
    assert params["entities"] == str(r["entities"])
    assert params["chunks"] == str(r["chunks"])
    assert params["coref_chains"] == str(r["coref_chains"])
    assert params["lexeme_occurrences"] == str(r["occurrences"])


def test_spacy_annotation_failure_leaves_no_visible_run(monkeypatch, pg):
    conn, created = pg
    doc_id = _itdb.make_doc(conn, TEXT)
    created.append(doc_id)

    def fail_copy(*args, **kwargs):
        raise RuntimeError("copy failed before provenance")

    monkeypatch.setattr(SpacyAnnotator, "_copy_layers", fail_copy)
    with pytest.raises(RuntimeError, match="copy failed"):
        SpacyAnnotator().annotate(conn, doc_id)

    rows = conn.execute(
        "SELECT layer FROM annotations WHERE document_id=%s AND layer IN ('spacy', 'lexeme')",
        (doc_id,),
    ).fetchall()
    events = conn.execute(
        "SELECT id FROM document_provenance WHERE document_id=%s AND kind='annotated'",
        (doc_id,),
    ).fetchall()
    assert rows == []
    assert events == []
