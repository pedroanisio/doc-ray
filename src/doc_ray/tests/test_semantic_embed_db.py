"""DB integration: ``KIND_SEMANTIC_EMBED`` writes learned sentence vectors.

Runs against the isolated test database with a deterministic fake encoder (no
model download). Verifies the per-model delete-then-COPY path, span-keyed rows
(``sentences.span_id = embedding.span_id`` — what the reader joins on), 768-dim
vectors, and idempotent re-embed. The pure L2-normalization test runs anywhere.
"""

from __future__ import annotations

import hashlib
from typing import Any

import _itdb

from doc_ray.application import semantic_embed
from doc_ray.application.semantic_embed import embed_document_sentences, to_vector_literal


class _FakeTextEncoder:
    """Deterministic 768-d content-hash encoder — no model, no dependency."""

    model_id = "doc-ray/sbert-mpnet-768"
    dim = 768

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            digest = hashlib.blake2b(text.encode("utf-8"), digest_size=32).digest()
            vectors.append([((digest[i % 32] + i) % 255) / 255.0 for i in range(self.dim)])
        return vectors


def test_to_vector_literal_is_l2_normalized() -> None:
    literal = to_vector_literal([3.0, 4.0])  # norm 5 → 0.6, 0.8
    assert literal == "[0.60000000,0.80000000]"
    # all-zero input must not divide by zero
    assert to_vector_literal([0.0, 0.0]) == "[0.00000000,0.00000000]"


class _FakeCopy:
    def __init__(self) -> None:
        self.rows: list[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False

    def write_row(self, row) -> None:
        self.rows.append(row)


class _RecordingCursor:
    """Cursor double: records copy() usage and flags any executemany fallback."""

    def __init__(self) -> None:
        self.copy_sql: str | None = None
        self.copy_stream = _FakeCopy()
        self.executemany_called = False

    def copy(self, sql: str):
        self.copy_sql = sql
        return self.copy_stream

    def executemany(self, *_a, **_k) -> None:
        self.executemany_called = True


def test_copy_rows_bulk_loads_via_copy_not_executemany() -> None:
    # The embedding write must be one COPY (bulk), not one INSERT per row: psycopg3
    # executemany still issues N server-side statements, and with the pgvector HNSW
    # index that per-row storm saturates the managed cluster.
    cur = _RecordingCursor()
    rows = [("s1", "doc-ray/sbert-mpnet-768", "[0.1]"), ("s2", "doc-ray/sbert-mpnet-768", "[0.2]")]
    semantic_embed._copy_rows(cur, "embedding", ("span_id", "model", "vector"), rows)
    assert cur.copy_sql == "COPY embedding (span_id, model, vector) FROM STDIN"
    assert cur.copy_stream.rows == rows
    assert cur.executemany_called is False


def test_copy_rows_is_noop_on_empty_rows() -> None:
    cur = _RecordingCursor()
    semantic_embed._copy_rows(cur, "embedding", ("span_id", "model", "vector"), [])
    assert cur.copy_sql is None
    assert cur.copy_stream.rows == []


def test_semantic_embed_writes_span_keyed_vectors(pg: Any) -> None:
    conn, created = pg
    doc_id = _itdb.make_doc(conn, "A red diagram. A second sentence about charts.")
    created.append(doc_id)

    encoder = _FakeTextEncoder()
    result = embed_document_sentences(conn, doc_id, encoder=encoder)
    assert result["model"] == encoder.model_id
    assert result["sentences_embedded"] >= 2

    # Rows land in `embedding`, tagged with the model, keyed by the sentence spans.
    n = conn.execute(
        """
        SELECT count(*) n
          FROM embedding e
          JOIN sentences s ON s.span_id = e.span_id
         WHERE s.document_id = %s AND e.model = %s
        """,
        (doc_id, encoder.model_id),
    ).fetchone()["n"]
    assert n == result["sentences_embedded"]

    # Vectors carry the encoder's dimension.
    dims = conn.execute(
        """
        SELECT DISTINCT vector_dims(e.vector) d
          FROM embedding e
          JOIN sentences s ON s.span_id = e.span_id
         WHERE s.document_id = %s AND e.model = %s
        """,
        (doc_id, encoder.model_id),
    ).fetchall()
    assert [row["d"] for row in dims] == [encoder.dim]

    # Idempotent per model: a re-embed replaces, never accumulates.
    again = embed_document_sentences(conn, doc_id, encoder=encoder)
    n2 = conn.execute(
        """
        SELECT count(*) n
          FROM embedding e
          JOIN sentences s ON s.span_id = e.span_id
         WHERE s.document_id = %s AND e.model = %s
        """,
        (doc_id, encoder.model_id),
    ).fetchone()["n"]
    assert n2 == again["sentences_embedded"] == result["sentences_embedded"]
