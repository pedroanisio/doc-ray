"""Migration 010: NER, entity linking, coreference (NLP layers 11, 12, 13)."""

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


def _mention(conn, ann, span_id, etype, surface):
    mid = new_ulid_str()
    conn.execute(
        "INSERT INTO entity_mention (id, annotation_id, span_id, entity_type, surface) "
        "VALUES (%s,%s,%s,%s,%s)",
        (mid, ann, span_id, etype, surface),
    )
    return mid


def test_tables_exist(pg):
    conn, _ = pg
    assert {"id", "annotation_id", "span_id", "entity_type", "surface"} <= _cols(
        conn, "entity_mention"
    )
    assert {"mention_id", "scheme", "kb_id", "confidence"} <= _cols(conn, "entity_link")
    assert {"id", "annotation_id", "document_id"} <= _cols(conn, "coref_chain")
    assert {"chain_id", "span_id"} <= _cols(conn, "coref_mention")


def test_ner_mentions_and_types(pg):
    """The surface text is a plain column read — no document_content join needed.

    Regression guard for the entity_frequency/list_entities incident: reads must
    never again reconstruct surface text via substring(full_text) at query time.
    """
    conn, reg = pg
    doc_id = _itdb.make_doc(conn, _itdb.EXAMPLE)
    reg.append(doc_id)
    ann = _itdb.make_annotation(conn, doc_id, "ner", "test/ner")
    elena = _itdb.span_of(conn, doc_id, 0, 5)  # "Elena"
    b = _itdb.EXAMPLE.index("Berlin")
    berlin = _itdb.span_of(conn, doc_id, b, b + 6)  # "Berlin"
    _mention(conn, ann, elena, "PER", "Elena")
    _mention(conn, ann, berlin, "GPE", "Berlin")
    got = {
        r["entity_type"]: r["surface"]
        for r in conn.execute(
            "SELECT entity_type, surface FROM entity_mention WHERE annotation_id=%s",
            (ann,),
        ).fetchall()
    }
    assert got == {"PER": "Elena", "GPE": "Berlin"}


def test_entity_link_and_nil_is_absence(pg):
    conn, reg = pg
    doc_id = _itdb.make_doc(conn, _itdb.EXAMPLE)
    reg.append(doc_id)
    ann = _itdb.make_annotation(conn, doc_id, "ner", "test/nel")
    b = _itdb.EXAMPLE.index("Berlin")
    berlin = _mention(conn, ann, _itdb.span_of(conn, doc_id, b, b + 6), "GPE", "Berlin")
    elena = _mention(
        conn, ann, _itdb.span_of(conn, doc_id, 0, 5), "PER", "Elena"
    )  # fictional -> NIL
    conn.execute(
        "INSERT INTO entity_link (mention_id, scheme, kb_id, confidence) "
        "VALUES (%s,'wikidata','Q64',0.99)",
        (berlin,),
    )
    rows = {
        r["id"]: r["kb_id"]
        for r in conn.execute(
            "SELECT m.id, l.kb_id FROM entity_mention m "
            "LEFT JOIN entity_link l ON l.mention_id=m.id WHERE m.annotation_id=%s",
            (ann,),
        ).fetchall()
    }
    assert rows[berlin] == "Q64"
    assert rows[elena] is None  # NIL = no link row


def test_entity_link_unique_per_scheme(pg):
    conn, reg = pg
    doc_id = _itdb.make_doc(conn, _itdb.EXAMPLE)
    reg.append(doc_id)
    ann = _itdb.make_annotation(conn, doc_id, "ner", "test/nel2")
    m = _mention(conn, ann, _itdb.span_of(conn, doc_id, 0, 5), "PER", "Elena")
    conn.execute(
        "INSERT INTO entity_link (mention_id, scheme, kb_id) VALUES (%s,'wikidata','Q1')", (m,)
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO entity_link (mention_id, scheme, kb_id) VALUES (%s,'wikidata','Q2')", (m,)
        )


def test_coref_chain_groups_mentions(pg):
    conn, reg = pg
    text = "Elena founded a startup. She left it."
    doc_id = _itdb.make_doc(conn, text)
    reg.append(doc_id)
    ann = _itdb.make_annotation(conn, doc_id, "coref", "test/coref")
    elena = _itdb.span_of(conn, doc_id, 0, 5)  # "Elena"
    she = _itdb.span_of(conn, doc_id, text.index("She"), text.index("She") + 3)
    chain = new_ulid_str()
    conn.execute(
        "INSERT INTO coref_chain (id, annotation_id, document_id) VALUES (%s,%s,%s)",
        (chain, ann, doc_id),
    )
    for span in (elena, she):
        conn.execute("INSERT INTO coref_mention (chain_id, span_id) VALUES (%s,%s)", (chain, span))
    members = conn.execute(
        "SELECT substring(c.full_text FROM sp.char_start+1 FOR sp.char_end-sp.char_start) txt "
        "FROM coref_mention cm JOIN spans sp ON sp.id=cm.span_id "
        "JOIN coref_chain ch ON ch.id=cm.chain_id "
        "JOIN document_content c ON c.document_id=ch.document_id "
        "WHERE cm.chain_id=%s ORDER BY sp.char_start",
        (chain,),
    ).fetchall()
    assert [m["txt"] for m in members] == ["Elena", "She"]
