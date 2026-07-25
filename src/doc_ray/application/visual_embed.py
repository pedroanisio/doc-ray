"""Prism Tier-2 orchestrator: embed a document's images + sentences (cross-modal).

The ``KIND_VISUAL_EMBED`` job body. Given a document that already has Tier-1
``document_image`` rows, it fetches each crop from the object store, embeds the
crops through the injected :class:`VisionEncoder`, and embeds the document's
sentences through the SAME model's text tower — writing both into the shared
``image_embedding`` / ``sentence_vision_embedding`` space so text->image and
image->image search work.

PALS posture: encoder output is used ONLY for ranking; it never asserts a fact
into the trusted graph. Writes are per-``model`` delete-then-COPY (idempotent
re-embed); the vector literal is L2-normalized, exactly like the existing
``embedding`` writer.
"""

from __future__ import annotations

import hashlib
import math
import os
from collections.abc import Sequence
from typing import Any, cast

import psycopg

from doc_ray.domain.progress import report_progress
from doc_ray.ports.object_store import ObjectStore
from doc_ray.ports.vision_encoder import VisionEncoder

_IMAGE_COLUMNS = ("document_image_id", "document_id", "model", "vector", "preprocessing_sha256")
_SENTENCE_COLUMNS = ("sentence_id", "document_id", "model", "vector")
_DEFAULT_BATCH_SIZE = 16


def _vision_batch_size() -> int:
    """Return the bounded encoder batch size configured for the worker."""
    raw = os.environ.get("DOCRAY_VISION_BATCH_SIZE", str(_DEFAULT_BATCH_SIZE))
    try:
        batch_size = int(raw)
    except ValueError as exc:
        raise ValueError("DOCRAY_VISION_BATCH_SIZE must be a positive integer") from exc
    if batch_size < 1:
        raise ValueError("DOCRAY_VISION_BATCH_SIZE must be a positive integer")
    return batch_size


def to_vector_literal(vec: Sequence[float]) -> str:
    """L2-normalize *vec* and format it as a pgvector text literal."""
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return "[" + ",".join(f"{v / norm:.8f}" for v in vec) + "]"


def _copy_rows(
    cur: psycopg.Cursor[Any], table: str, columns: tuple[str, ...], rows: Sequence[tuple[Any, ...]]
) -> None:
    """Bulk-insert rows via COPY (no-op on empty input).

    The write-amplification-safe path for the HNSW-indexed vector tables
    (proposal §5.5): COPY, never per-row INSERT.
    """
    if not rows:
        return
    collist = ", ".join(columns)
    with cur.copy(f"COPY {table} ({collist}) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)


def embed_document_visuals(
    conn: psycopg.Connection[Any],
    store: ObjectStore,
    document_id: str,
    *,
    encoder: VisionEncoder,
) -> dict[str, Any]:
    """Embed a document's images + sentences into the shared space. Idempotent per model."""
    model = encoder.model_id
    batch_size = _vision_batch_size()
    report_progress(0, 3, "Loading image crops")
    image_cur = conn.execute(
        "SELECT id, object_key FROM document_image WHERE document_id = %s ORDER BY id",
        (document_id,),
    )
    image_meta = [(str(r["id"]), str(r["object_key"])) for r in image_cur.fetchall()]

    report_progress(1, 3, f"Embedding {len(image_meta)} images")
    image_rows: list[tuple[Any, ...]] = []
    for offset in range(0, len(image_meta), batch_size):
        metadata_batch = image_meta[offset : offset + batch_size]
        crops_batch = [store.get(key) for _, key in metadata_batch]
        vectors_batch = encoder.embed_images(crops_batch)
        image_rows.extend(
            (
                image_id,
                document_id,
                model,
                to_vector_literal(vector),
                hashlib.sha256(crop).hexdigest(),
            )
            for (image_id, _), crop, vector in zip(
                metadata_batch, crops_batch, vectors_batch, strict=True
            )
        )

    report_progress(2, 3, "Embedding sentences (text tower)")
    sentence_cur = conn.execute(
        "SELECT id, text FROM sentences WHERE document_id = %s ORDER BY ordinal, id",
        (document_id,),
    )
    sentence_meta = [(str(r["id"]), str(r["text"])) for r in sentence_cur.fetchall()]
    sentence_rows: list[tuple[Any, ...]] = []
    for offset in range(0, len(sentence_meta), batch_size):
        metadata_batch = sentence_meta[offset : offset + batch_size]
        vectors_batch = encoder.embed_texts([text for _, text in metadata_batch])
        sentence_rows.extend(
            (sentence_id, document_id, model, to_vector_literal(vector))
            for (sentence_id, _), vector in zip(metadata_batch, vectors_batch, strict=True)
        )

    with conn.transaction():
        conn.execute(
            "DELETE FROM image_embedding WHERE document_id = %s AND model = %s",
            (document_id, model),
        )
        conn.execute(
            "DELETE FROM sentence_vision_embedding WHERE document_id = %s AND model = %s",
            (document_id, model),
        )
        with conn.cursor() as cur:
            _copy_rows(cur, "image_embedding", _IMAGE_COLUMNS, image_rows)
            _copy_rows(cur, "sentence_vision_embedding", _SENTENCE_COLUMNS, sentence_rows)

    return {
        "model": model,
        "images_embedded": len(image_rows),
        "sentences_embedded": len(sentence_rows),
    }


def cross_modal_image_search(
    conn: psycopg.Connection[Any],
    query_text: str,
    *,
    encoder: VisionEncoder,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Return images ranked by cosine similarity to *query_text* (text->image).

    Embeds the query through the SAME model's text tower and ranks
    ``image_embedding`` by HNSW cosine distance. RLS-scoped: only images the
    caller may read are visible.
    """
    query_vec = to_vector_literal(encoder.embed_texts([query_text])[0])
    cur = conn.execute(
        """
        SELECT di.id, di.document_id, di.page_number, di.source, di.object_key,
               f.image_class,
               1 - (ie.vector <=> %(q)s::vector) AS similarity
        FROM image_embedding ie
        JOIN document_image di ON di.id = ie.document_image_id
        LEFT JOIN image_tier1_features f ON f.document_image_id = di.id
        WHERE ie.model = %(model)s
        ORDER BY ie.vector <=> %(q)s::vector
        LIMIT %(limit)s
        """,
        {"q": query_vec, "model": encoder.model_id, "limit": limit},
    )
    return cast("list[dict[str, Any]]", cur.fetchall())
