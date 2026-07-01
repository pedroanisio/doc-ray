"""Migration 008: tokens + morphology (FEATS) + subwords (NLP layers 3,4,6,7)."""

from __future__ import annotations

import _itdb
import pytest


def _cols(conn, table):
    return {
        r["column_name"]
        for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name=%s", (table,)
        ).fetchall()
    }


def test_tables_exist(pg):
    conn, _ = pg
    assert {"id", "annotation_id", "span_id", "ordinal", "lexeme_uid", "upos", "xpos"} <= _cols(
        conn, "tokens"
    )
    assert {"token_id", "feature", "value"} <= _cols(conn, "token_feature")
    assert {"token_id", "position", "piece"} <= _cols(conn, "subword")


def test_tokens_map_to_word_spans(pg):
    conn, reg = pg
    doc_id = _itdb.make_doc(conn, _itdb.EXAMPLE)
    reg.append(doc_id)
    ann = _itdb.make_annotation(conn, doc_id, "token", "test/tokenizer")
    toks = _itdb.make_tokens(conn, doc_id, ann, _itdb.EXAMPLE)
    assert [t["surface"] for t in toks] == ["Elena", "founded", "a", "startup", "in", "Berlin"]
    # each token's span recovers its surface from full_text
    rows = conn.execute(
        "SELECT t.ordinal, substring(c.full_text FROM sp.char_start+1 FOR sp.char_end-sp.char_start) surface "
        "FROM tokens t JOIN spans sp ON sp.id=t.span_id "
        "JOIN annotations a ON a.id=t.annotation_id "
        "JOIN document_content c ON c.document_id=a.document_id "
        "WHERE t.annotation_id=%s ORDER BY t.ordinal",
        (ann,),
    ).fetchall()
    assert [r["surface"] for r in rows] == ["Elena", "founded", "a", "startup", "in", "Berlin"]


def test_morphological_features_are_rows(pg):
    conn, reg = pg
    doc_id = _itdb.make_doc(conn, _itdb.EXAMPLE)
    reg.append(doc_id)
    ann = _itdb.make_annotation(conn, doc_id, "morph", "test/tagger")
    toks = _itdb.make_tokens(conn, doc_id, ann, _itdb.EXAMPLE)
    founded = next(t for t in toks if t["surface"] == "founded")
    # FEATS: Mood=Ind|Tense=Past|VerbForm=Fin -> three rows
    for feat, val in [("Mood", "Ind"), ("Tense", "Past"), ("VerbForm", "Fin")]:
        conn.execute(
            "INSERT INTO token_feature (token_id, feature, value) VALUES (%s,%s,%s)",
            (founded["id"], feat, val),
        )
    got = dict(
        r.values()
        for r in conn.execute(
            "SELECT feature, value FROM token_feature WHERE token_id=%s ORDER BY feature",
            (founded["id"],),
        ).fetchall()
    )
    assert got == {"Mood": "Ind", "Tense": "Past", "VerbForm": "Fin"}


def test_feature_key_is_unique_per_token(pg):
    conn, reg = pg
    doc_id = _itdb.make_doc(conn, _itdb.EXAMPLE)
    reg.append(doc_id)
    ann = _itdb.make_annotation(conn, doc_id, "morph", "test/tagger2")
    tok = _itdb.make_tokens(conn, doc_id, ann, _itdb.EXAMPLE)[0]
    conn.execute(
        "INSERT INTO token_feature (token_id, feature, value) VALUES (%s,'Number','Sing')",
        (tok["id"],),
    )
    import psycopg

    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO token_feature (token_id, feature, value) VALUES (%s,'Number','Plur')",
            (tok["id"],),
        )


def test_subwords_are_ordered_pieces(pg):
    conn, reg = pg
    doc_id = _itdb.make_doc(conn, _itdb.EXAMPLE)
    reg.append(doc_id)
    ann = _itdb.make_annotation(conn, doc_id, "subword", "test/wordpiece")
    tok = next(
        t for t in _itdb.make_tokens(conn, doc_id, ann, _itdb.EXAMPLE) if t["surface"] == "startup"
    )
    for pos, piece in enumerate(["start", "##up"]):
        conn.execute(
            "INSERT INTO subword (token_id, position, piece) VALUES (%s,%s,%s)",
            (tok["id"], pos, piece),
        )
    pieces = [
        r["piece"]
        for r in conn.execute(
            "SELECT piece FROM subword WHERE token_id=%s ORDER BY position", (tok["id"],)
        ).fetchall()
    ]
    assert pieces == ["start", "##up"]
