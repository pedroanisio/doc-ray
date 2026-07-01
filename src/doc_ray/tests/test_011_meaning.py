"""Migration 011: SRL, WSD, AMR, discourse, temporal, sentiment (layers 14-19)."""

from __future__ import annotations

import _itdb
import pytest
from doc_ray.domain.ulid import new_ulid_str


def _cols(conn, table):
    return {r["column_name"] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name=%s",
        (table,)).fetchall()}


def _tokens(conn, reg, layer, tool, text=_itdb.EXAMPLE):
    doc_id = _itdb.make_doc(conn, text); reg.append(doc_id)
    ann = _itdb.make_annotation(conn, doc_id, layer, tool)
    toks = {t["surface"]: t for t in _itdb.make_tokens(conn, doc_id, ann, text)}
    return doc_id, ann, toks


def test_tables_exist(pg):
    conn, _ = pg
    assert {"id", "annotation_id", "token_id", "frame"} <= _cols(conn, "srl_predicate")
    assert {"predicate_id", "span_id", "role"} <= _cols(conn, "srl_argument")
    assert {"token_id", "annotation_id", "sense_key"} <= _cols(conn, "word_sense")
    assert {"id", "annotation_id", "concept"} <= _cols(conn, "amr_node")
    assert {"id", "parent_node_id", "child_node_id", "role"} <= _cols(conn, "amr_edge")
    assert {"id", "annotation_id", "arg1_span_id", "arg2_span_id", "sense"} <= _cols(conn, "discourse_relation")
    assert {"id", "annotation_id", "span_id", "timex_type", "value"} <= _cols(conn, "timex")
    assert {"id", "annotation_id", "token_id", "event_class"} <= _cols(conn, "event")
    assert {"id", "annotation_id", "source_id", "target_id", "relation"} <= _cols(conn, "tlink")
    assert {"id", "annotation_id", "span_id", "polarity", "score"} <= _cols(conn, "sentiment")


def test_srl_predicate_and_arguments(pg):
    conn, reg = pg
    doc_id, ann, toks = _tokens(conn, reg, "srl", "test/srl")
    pred = new_ulid_str()
    conn.execute("INSERT INTO srl_predicate (id, annotation_id, token_id, frame) VALUES (%s,%s,%s,'found.01')",
                 (pred, ann, toks["founded"]["id"]))
    a1 = _itdb.EXAMPLE.index("a startup")
    args = [("ARG0", toks["Elena"]["span_id"]),
            ("ARG1", _itdb.span_of(conn, doc_id, a1, a1 + len("a startup"))),
            ("ARGM-LOC", _itdb.span_of(conn, doc_id, _itdb.EXAMPLE.index("in Berlin"),
                                       _itdb.EXAMPLE.index("in Berlin") + len("in Berlin")))]
    for role, span in args:
        conn.execute("INSERT INTO srl_argument (predicate_id, span_id, role) VALUES (%s,%s,%s)",
                     (pred, span, role))
    got = {r["role"] for r in conn.execute(
        "SELECT role FROM srl_argument WHERE predicate_id=%s", (pred,)).fetchall()}
    assert got == {"ARG0", "ARG1", "ARGM-LOC"}
    frame = conn.execute("SELECT frame FROM srl_predicate WHERE id=%s", (pred,)).fetchone()["frame"]
    assert frame == "found.01"


def test_word_sense(pg):
    conn, reg = pg
    _, ann, toks = _tokens(conn, reg, "wsd", "test/wsd")
    conn.execute("INSERT INTO word_sense (token_id, annotation_id, sense_key) VALUES (%s,%s,'found.v.01')",
                 (toks["founded"]["id"], ann))
    sense = conn.execute("SELECT sense_key FROM word_sense WHERE token_id=%s", (toks["founded"]["id"],)).fetchone()
    assert sense["sense_key"] == "found.v.01"


def test_amr_graph_allows_reentrancy(pg):
    conn, reg = pg
    doc_id = _itdb.make_doc(conn, _itdb.EXAMPLE); reg.append(doc_id)
    ann = _itdb.make_annotation(conn, doc_id, "amr", "test/amr")
    f, p, s = new_ulid_str(), new_ulid_str(), new_ulid_str()
    for nid, concept in [(f, "found-01"), (p, "person"), (s, "startup")]:
        conn.execute("INSERT INTO amr_node (id, annotation_id, concept) VALUES (%s,%s,%s)", (nid, ann, concept))
    conn.execute("INSERT INTO amr_edge (id, parent_node_id, child_node_id, role) VALUES (%s,%s,%s,'ARG0')",
                 (new_ulid_str(), f, p))
    conn.execute("INSERT INTO amr_edge (id, parent_node_id, child_node_id, role) VALUES (%s,%s,%s,'ARG1')",
                 (new_ulid_str(), f, s))
    # reentrancy: the same node can be the child of a second edge
    conn.execute("INSERT INTO amr_edge (id, parent_node_id, child_node_id, role) VALUES (%s,%s,%s,'poss')",
                 (new_ulid_str(), s, p))
    parents = conn.execute("SELECT count(DISTINCT parent_node_id) n FROM amr_edge WHERE child_node_id=%s", (p,)).fetchone()["n"]
    assert parents == 2                                  # p is reachable from two parents


def test_discourse_relation(pg):
    conn, reg = pg
    text = "The company grew because investors trusted her."
    doc_id = _itdb.make_doc(conn, text); reg.append(doc_id)
    ann = _itdb.make_annotation(conn, doc_id, "discourse", "test/pdtb")
    conn_span = _itdb.span_of(conn, doc_id, text.index("because"), text.index("because") + 7)
    arg1 = _itdb.span_of(conn, doc_id, 0, text.index(" because"))
    arg2 = _itdb.span_of(conn, doc_id, text.index("investors"), len(text) - 1)
    rid = new_ulid_str()
    conn.execute("INSERT INTO discourse_relation (id, annotation_id, connective_span_id, arg1_span_id, arg2_span_id, sense) "
                 "VALUES (%s,%s,%s,%s,%s,'Contingency.Cause.Reason')", (rid, ann, conn_span, arg1, arg2))
    row = conn.execute("SELECT sense FROM discourse_relation WHERE id=%s", (rid,)).fetchone()
    assert row["sense"] == "Contingency.Cause.Reason"


def test_timeml_event_timex_tlink(pg):
    conn, reg = pg
    text = "In March 2023 Elena founded a startup."
    doc_id, ann, toks = _tokens(conn, reg, "timeml", "test/timeml", text)
    m = text.index("March 2023")
    tx = new_ulid_str(); ev = new_ulid_str()
    conn.execute("INSERT INTO timex (id, annotation_id, span_id, timex_type, value) VALUES (%s,%s,%s,'DATE','2023-03')",
                 (tx, ann, _itdb.span_of(conn, doc_id, m, m + len("March 2023"))))
    conn.execute("INSERT INTO event (id, annotation_id, token_id, event_class) VALUES (%s,%s,%s,'OCCURRENCE')",
                 (ev, ann, toks["founded"]["id"]))
    conn.execute("INSERT INTO tlink (id, annotation_id, source_id, target_id, relation) VALUES (%s,%s,%s,%s,'IS_INCLUDED')",
                 (new_ulid_str(), ann, ev, tx))
    link = conn.execute("SELECT source_id, target_id, relation FROM tlink WHERE annotation_id=%s", (ann,)).fetchone()
    assert link["source_id"] == ev and link["target_id"] == tx and link["relation"] == "IS_INCLUDED"


def test_sentiment_span(pg):
    conn, reg = pg
    text = "Elena remains optimistic."
    doc_id = _itdb.make_doc(conn, text); reg.append(doc_id)
    ann = _itdb.make_annotation(conn, doc_id, "sentiment", "test/senti")
    span = _itdb.span_of(conn, doc_id, text.index("optimistic"), text.index("optimistic") + len("optimistic"))
    conn.execute("INSERT INTO sentiment (id, annotation_id, span_id, polarity, score) VALUES (%s,%s,%s,'POSITIVE',0.9)",
                 (new_ulid_str(), ann, span))
    row = conn.execute("SELECT polarity, score FROM sentiment WHERE annotation_id=%s", (ann,)).fetchone()
    assert row["polarity"] == "POSITIVE" and float(row["score"]) == pytest.approx(0.9)
