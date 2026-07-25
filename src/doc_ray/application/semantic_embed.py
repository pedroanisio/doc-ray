"""Learned sentence-embedding writer (NLP Layer 20) — replaces hash-BoW.

The ``KIND_SEMANTIC_EMBED`` job body. Given a document that already has
``sentences`` rows, it embeds each sentence through the injected
:class:`~doc_ray.ports.text_encoder.TextEncoder` and writes one row into the
shared ``embedding`` table, keyed by the sentence's span and tagged with the
encoder's ``model_id`` — the same span identity the semantic-search reader joins
on (``sentences.span_id = embedding.span_id``).

This is the sole writer of ``embedding`` after the hard cutover from hash-BoW.

PALS posture: encoder output is used ONLY for ranking; it never asserts a fact
into the trusted graph. Writes are per-``model`` delete-then-COPY (idempotent
re-embed); the vector literal is L2-normalized, exactly like the ``visual_embed``
writer.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import psycopg

from doc_ray.domain.jobs import Job
from doc_ray.domain.progress import report_progress
from doc_ray.ports.object_store import ObjectStore
from doc_ray.ports.text_encoder import TextEncoder

_EMBEDDING_COLUMNS = ("span_id", "model", "vector")


def to_vector_literal(vec: Sequence[float]) -> str:
    """L2-normalize *vec* and format it as a pgvector text literal."""
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return "[" + ",".join(f"{v / norm:.8f}" for v in vec) + "]"


def _copy_rows(
    cur: psycopg.Cursor[Any], table: str, columns: tuple[str, ...], rows: Sequence[tuple[Any, ...]]
) -> None:
    """Bulk-insert rows via COPY (no-op on empty input).

    The write-amplification-safe path for the HNSW-indexed ``embedding`` table:
    COPY, never per-row INSERT. Callers clear conflicting rows first.
    """
    if not rows:
        return
    collist = ", ".join(columns)
    with cur.copy(f"COPY {table} ({collist}) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)


def embed_document_sentences(
    conn: psycopg.Connection[Any], document_id: str, *, encoder: TextEncoder
) -> dict[str, Any]:
    """Embed a document's sentences into ``embedding``. Idempotent per model."""
    model = encoder.model_id
    report_progress(0, 2, "Loading sentences")
    rows = conn.execute(
        """
        SELECT span_id, text
          FROM sentences
         WHERE document_id = %s AND span_id IS NOT NULL
         ORDER BY ordinal, id
        """,
        (document_id,),
    ).fetchall()
    sentence_meta = [(str(r["span_id"]), str(r["text"])) for r in rows]

    report_progress(1, 2, f"Embedding {len(sentence_meta)} sentences")
    vectors = encoder.embed_texts([text for _, text in sentence_meta]) if sentence_meta else []
    embedding_rows = [
        (span_id, model, to_vector_literal(vec))
        for (span_id, _), vec in zip(sentence_meta, vectors, strict=True)
    ]

    with conn.transaction():
        # Per-model replacement: clear this document's rows for this model, then
        # bulk COPY. Keeps re-embed idempotent and never accumulates duplicates.
        conn.execute(
            """
            DELETE FROM embedding e
             USING spans sp
             WHERE e.span_id = sp.id
               AND sp.document_id = %s
               AND e.model = %s
            """,
            (document_id, model),
        )
        with conn.cursor() as cur:
            _copy_rows(cur, "embedding", _EMBEDDING_COLUMNS, embedding_rows)

    return {"model": model, "sentences_embedded": len(embedding_rows)}


def handle_semantic_embed(
    conn: psycopg.Connection[Any], store: ObjectStore | None, job: Job
) -> dict[str, Any]:
    """``KIND_SEMANTIC_EMBED`` job body: embed a document's sentences (worker-only).

    Co-located with the writer (kept out of ``job_handlers`` for the D-015 cap).
    ``build_text_encoder`` fails typed-and-actionable when the sentence stack is
    absent instead of leaking a raw ``ModuleNotFoundError``.
    """
    from doc_ray.adapters.text_encoder_sentence_transformers import build_text_encoder

    if job.document_id is None:
        raise RuntimeError("semantic_embed requires a document_id")
    encoder = build_text_encoder()
    report_progress(0, 1, "Embedding document sentences")
    return embed_document_sentences(conn, job.document_id, encoder=encoder)
