"""Migration 012: distributed representations / embeddings (NLP layer 20)."""

from __future__ import annotations

import _itdb
import psycopg
import pytest
from ingest.ulid import new_ulid_str

DIM = 768


def _vec(idx: int, val: float = 1.0) -> str:
    v = [0.0] * DIM
    v[idx] = val
    return "[" + ",".join(str(x) for x in v) + "]"


def _cols(conn, table):
    return {r["column_name"] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name=%s",
        (table,)).fetchall()}


def test_table_exists(pg):
    conn, _ = pg
    assert {"span_id", "model", "vector"} <= _cols(conn, "embedding")


def test_store_and_dimension(pg):
    conn, reg = pg
    doc_id = _itdb.make_doc(conn, _itdb.EXAMPLE); reg.append(doc_id)
    span = _itdb.span_of(conn, doc_id, 0, 5)
    conn.execute("INSERT INTO embedding (span_id, model, vector) VALUES (%s,'m1',%s)", (span, _vec(3)))
    d = conn.execute("SELECT vector_dims(vector) d FROM embedding WHERE span_id=%s", (span,)).fetchone()["d"]
    assert d == DIM


def test_unique_per_span_and_model(pg):
    conn, reg = pg
    doc_id = _itdb.make_doc(conn, _itdb.EXAMPLE); reg.append(doc_id)
    span = _itdb.span_of(conn, doc_id, 0, 5)
    conn.execute("INSERT INTO embedding (span_id, model, vector) VALUES (%s,'m',%s)", (span, _vec(0)))
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute("INSERT INTO embedding (span_id, model, vector) VALUES (%s,'m',%s)", (span, _vec(1)))
    # a different model for the same span is allowed
    conn.execute("INSERT INTO embedding (span_id, model, vector) VALUES (%s,'m2',%s)", (span, _vec(1)))


def test_cosine_nearest_neighbor(pg):
    conn, reg = pg
    doc_id = _itdb.make_doc(conn, _itdb.EXAMPLE); reg.append(doc_id)
    model = "t" + new_ulid_str()[:8]
    s_elena = _itdb.span_of(conn, doc_id, 0, 5)
    b = _itdb.EXAMPLE.index("Berlin")
    s_berlin = _itdb.span_of(conn, doc_id, b, b + 6)
    conn.execute("INSERT INTO embedding (span_id, model, vector) VALUES (%s,%s,%s)", (s_elena, model, _vec(0)))
    conn.execute("INSERT INTO embedding (span_id, model, vector) VALUES (%s,%s,%s)", (s_berlin, model, _vec(1)))
    nearest = conn.execute(
        "SELECT span_id FROM embedding WHERE model=%s ORDER BY vector <=> %s::vector LIMIT 1",
        (model, _vec(0))).fetchone()
    assert nearest["span_id"] == s_elena
