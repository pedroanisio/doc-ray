"""Async read adapter for the Prism visual layers.

Mirrors ``persistence_layers_read`` (same ``_AConn``/``Row``/keyset-cursor
conventions) but for the image tables, kept separate so the visual layer's
readers can grow (Tier-2 embeddings, Tier-3 captions) without bloating the NLP
reader module past the D-015 cap.
"""

from __future__ import annotations

from typing import Any

import psycopg

from doc_ray.adapters.read_cursors import decode_cursor, encode_cursor

Row = dict[str, Any]
_AConn = psycopg.AsyncConnection[Row]


async def list_document_images(
    conn: _AConn, document_id: str, *, limit: int = 100, after: str | None = None
) -> list[Row]:
    """Return detected images + their deterministic Tier-1 features for one document.

    Left-joins ``image_tier1_features`` so an image with no features yet still
    appears. Keyset-paginated by ``(page_number, id)`` for stable page order.
    """
    cursor = decode_cursor(after, "document-images")
    cur = await conn.execute(
        """
        SELECT di.id, di.document_id, di.page_number, di.source, di.xref,
               di.bbox_x0, di.bbox_y0, di.bbox_x1, di.bbox_y1,
               di.width_px, di.height_px, di.object_key, di.sha256,
               di.detector, di.detector_version, di.created_at,
               f.dominant_colors, f.histogram, f.mean_saturation, f.mean_value,
               f.contrast, f.edge_density, f.image_class, f.aspect_ratio, f.exif,
               f.tool AS feature_tool, f.tool_version AS feature_tool_version
        FROM document_image di
        LEFT JOIN image_tier1_features f ON f.document_image_id = di.id
        WHERE di.document_id = %(doc)s::uuid
          AND (%(after_page)s::int IS NULL
               OR (di.page_number, di.id) > (%(after_page)s::int, %(after_id)s::ulid))
        ORDER BY di.page_number, di.id
        LIMIT %(limit)s
        """,
        {
            "doc": document_id,
            "limit": limit,
            "after_page": cursor["page_number"] if cursor else None,
            "after_id": cursor["id"] if cursor else None,
        },
    )
    rows = await cur.fetchall()
    for row in rows:
        row["cursor"] = encode_cursor(
            "document-images", page_number=row["page_number"], id=row["id"]
        )
    return rows


async def image_class_summary(conn: _AConn, document_id: str) -> list[Row]:
    """Return ``(image_class, count)`` for a document's Tier-1 features, most first."""
    cur = await conn.execute(
        """
        SELECT f.image_class, count(*)::int AS count
        FROM image_tier1_features f
        WHERE f.document_id = %(doc)s::uuid
        GROUP BY f.image_class
        ORDER BY count DESC, f.image_class
        """,
        {"doc": document_id},
    )
    return await cur.fetchall()


async def cross_modal_image_search(
    conn: _AConn, query_vector: str, model: str, *, limit: int = 25
) -> list[Row]:
    """Return images ranked by cosine similarity to a pre-embedded text query vector.

    ``query_vector`` is a pgvector literal from the same model's text tower. RLS
    scopes the result to images the caller may read.
    """
    cur = await conn.execute(
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
        {"q": query_vector, "model": model, "limit": limit},
    )
    return await cur.fetchall()


async def list_visual_captions(conn: _AConn, document_id: str, *, limit: int = 100) -> list[Row]:
    """Return a document's COMMITTED Tier-3 captions (never the review/rejected ones).

    Only ``status='committed'`` is trusted output — low-confidence captions live in
    ``visual_review`` and are deliberately not exposed here.
    """
    cur = await conn.execute(
        """
        SELECT vc.id, vc.document_id, vc.document_image_id, vc.caption, vc.model,
               vc.grounding_score, di.page_number, di.object_key
        FROM visual_caption vc
        JOIN document_image di ON di.id = vc.document_image_id
        WHERE vc.document_id = %(doc)s::uuid AND vc.status = 'committed'
        ORDER BY di.page_number, vc.id
        LIMIT %(limit)s
        """,
        {"doc": document_id, "limit": limit},
    )
    return await cur.fetchall()
