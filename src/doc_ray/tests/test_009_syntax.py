"""Migration 009: chunking, constituency, dependencies (NLP layers 8, 9, 10)."""

from __future__ import annotations

import _itdb
import psycopg
import pytest

from doc_ray.domain.ulid import new_ulid_str


def _cols(conn, table):
    return {
        r["column_name"]
        for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name=%s", (table,)
        ).fetchall()
    }


# The dependency tree of "Elena founded a startup in Berlin."
# (dependent surface, head surface or None=root, deprel)
DEPS = [
    ("Elena", "founded", "nsubj"),
    ("founded", None, "root"),
    ("a", "startup", "det"),
    ("startup", "founded", "obj"),
    ("in", "Berlin", "case"),
    ("Berlin", "founded", "obl"),
]


def test_tables_exist(pg):
    conn, _ = pg
    assert {"id", "annotation_id", "span_id", "chunk_type"} <= _cols(conn, "chunk")
    assert {"id", "annotation_id", "parent_id", "label", "span_id"} <= _cols(conn, "constituent")
    assert {"annotation_id", "dependent_token_id", "head_token_id", "deprel"} <= _cols(
        conn, "dependency"
    )


def _setup_tokens(conn, reg, layer, tool):
    doc_id = _itdb.make_doc(conn, _itdb.EXAMPLE)
    reg.append(doc_id)
    ann = _itdb.make_annotation(conn, doc_id, layer, tool)
    toks = {t["surface"]: t for t in _itdb.make_tokens(conn, doc_id, ann, _itdb.EXAMPLE)}
    return doc_id, ann, toks


def test_dependency_tree_roundtrips(pg):
    conn, reg = pg
    doc_id, ann, toks = _setup_tokens(conn, reg, "syntax", "test/depparser")
    for dep, head, rel in DEPS:
        conn.execute(
            "INSERT INTO dependency (annotation_id, dependent_token_id, head_token_id, deprel) "
            "VALUES (%s, %s, %s, %s)",
            (ann, toks[dep]["id"], toks[head]["id"] if head else None, rel),
        )
    # exactly one root, and it is 'founded'
    root = conn.execute(
        "SELECT dependent_token_id FROM dependency "
        "WHERE annotation_id=%s AND head_token_id IS NULL",
        (ann,),
    ).fetchall()
    assert len(root) == 1 and root[0]["dependent_token_id"] == toks["founded"]["id"]
    # every dependent has its expected (head, deprel)
    for dep, head, rel in DEPS:
        row = conn.execute(
            "SELECT head_token_id, deprel FROM dependency "
            "WHERE annotation_id=%s AND dependent_token_id=%s",
            (ann, toks[dep]["id"]),
        ).fetchone()
        assert row["deprel"] == rel
        assert row["head_token_id"] == (toks[head]["id"] if head else None)


def test_token_has_at_most_one_head(pg):
    conn, reg = pg
    _, ann, toks = _setup_tokens(conn, reg, "syntax", "test/depparser2")
    conn.execute(
        "INSERT INTO dependency (annotation_id, dependent_token_id, head_token_id, deprel) "
        "VALUES (%s,%s,%s,'obj')",
        (ann, toks["startup"]["id"], toks["founded"]["id"]),
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO dependency (annotation_id, dependent_token_id, head_token_id, deprel) "
            "VALUES (%s,%s,%s,'nsubj')",
            (ann, toks["startup"]["id"], toks["Elena"]["id"]),
        )


def test_constituency_tree_nests(pg):
    conn, reg = pg
    doc_id, ann, toks = _setup_tokens(conn, reg, "constituency", "test/parser")
    sent_span = _itdb.span_of(conn, doc_id, 0, len(_itdb.EXAMPLE) - 1)
    s = new_ulid_str()
    np = new_ulid_str()
    vp = new_ulid_str()
    conn.execute(
        "INSERT INTO constituent (id, annotation_id, parent_id, label, span_id) VALUES (%s,%s,NULL,'S',%s)",
        (s, ann, sent_span),
    )
    conn.execute(
        "INSERT INTO constituent (id, annotation_id, parent_id, label, span_id) VALUES (%s,%s,%s,'NP',%s)",
        (np, ann, s, toks["Elena"]["span_id"]),
    )
    conn.execute(
        "INSERT INTO constituent (id, annotation_id, parent_id, label, span_id) VALUES (%s,%s,%s,'VP',%s)",
        (vp, ann, s, toks["founded"]["span_id"]),
    )
    kids = conn.execute(
        "SELECT label FROM constituent WHERE parent_id=%s ORDER BY label", (s,)
    ).fetchall()
    assert [k["label"] for k in kids] == ["NP", "VP"]
    root = conn.execute(
        "SELECT label FROM constituent WHERE annotation_id=%s AND parent_id IS NULL", (ann,)
    ).fetchone()
    assert root["label"] == "S"


def test_chunks_carry_type_and_span(pg):
    conn, reg = pg
    doc_id, ann, toks = _setup_tokens(conn, reg, "chunk", "test/chunker")
    start = _itdb.EXAMPLE.index("a startup")
    np_span = _itdb.span_of(conn, doc_id, start, start + len("a startup"))
    conn.execute(
        "INSERT INTO chunk (id, annotation_id, span_id, chunk_type) VALUES (%s,%s,%s,%s)",
        (new_ulid_str(), ann, np_span, "NP"),
    )
    row = conn.execute(
        "SELECT ch.chunk_type, substring(c.full_text FROM sp.char_start+1 FOR sp.char_end-sp.char_start) txt "
        "FROM chunk ch JOIN spans sp ON sp.id=ch.span_id "
        "JOIN annotations a ON a.id=ch.annotation_id "
        "JOIN document_content c ON c.document_id=a.document_id WHERE ch.annotation_id=%s",
        (ann,),
    ).fetchone()
    assert row["chunk_type"] == "NP" and row["txt"] == "a startup"
