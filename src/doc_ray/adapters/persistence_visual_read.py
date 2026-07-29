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
    # HNSW returns a bounded candidate set BEFORE the RLS/model filters apply, so a
    # plain top-N can silently under-return for a non-admin principal. pgvector 0.8+
    # iterative scan keeps scanning the index until LIMIT rows pass the filters.
    async with conn.transaction():
        await conn.execute("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")
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


#: Ordering the review queue supports. An allowlist, not caller text: the value
#: reaches an ORDER BY, and a keyset cursor is only correct for an order the
#: cursor was built for.
_SORTS: dict[str, str] = {
    "grounding_desc": "vr.grounding_score DESC, vr.created_at DESC, vr.id DESC",
    "grounding_asc": "vr.grounding_score ASC, vr.created_at DESC, vr.id DESC",
    "newest": "vr.created_at DESC, vr.id DESC",
    "oldest": "vr.created_at ASC, vr.id ASC",
}

#: Highest grounding first. The queue previously returned newest-first, so the
#: first screen was an unrepresentative sample of the worst items and an operator
#: reasonably concluded the whole corpus was noise (BL-197). Surfacing the most
#: approvable work first is what makes a session's effort productive.
DEFAULT_REVIEW_SORT = "grounding_desc"


def _sort_clause(sort: str) -> str:
    """Map a sort name to its ORDER BY clause, failing closed on anything else.

    Raises:
        ValueError: The name is not one of :data:`_SORTS`.
    """
    try:
        return _SORTS[sort]
    except KeyError:
        raise ValueError(
            f"unknown visual review sort {sort!r}; expected one of {sorted(_SORTS)}"
        ) from None


async def list_visual_review(
    conn: _AConn,
    *,
    document_id: str | None = None,
    status: str = "pending",
    limit: int = 100,
    after: str | None = None,
    sort: str = DEFAULT_REVIEW_SORT,
    min_grounding: float | None = None,
    max_grounding: float | None = None,
) -> list[Row]:
    """Return the RLS-scoped Tier-3 caption review queue.

    ``sort`` selects the order (see :data:`_SORTS`); ``min_grounding`` and
    ``max_grounding`` bound the grounding band. The band is applied in SQL rather
    than after the page is cut, so a filtered page is a full page of matches
    instead of a short page with matching rows stranded behind it.

    Keyset pagination stays correct only within one order, so ``after`` cursors
    are not portable across a change of ``sort``.
    """
    if status not in {"pending", "approved", "rejected"}:
        raise ValueError("visual review status must be pending, approved, or rejected")
    order_by = _sort_clause(sort)
    cursor = decode_cursor(after, "visual-review")
    cur = await conn.execute(
        f"""
        SELECT vr.id, vr.document_image_id, vr.document_id, vr.proposed,
               vr.grounding_score, vr.grounding_evidence, vr.status, vr.reviewer,
               vr.created_at, vr.updated_at, di.page_number, di.object_key
        FROM visual_review vr
        JOIN document_image di ON di.id = vr.document_image_id
        WHERE vr.status = %(status)s
          AND (%(doc)s::uuid IS NULL OR vr.document_id = %(doc)s::uuid)
          AND (%(min_grounding)s::float8 IS NULL
               OR vr.grounding_score >= %(min_grounding)s::float8)
          AND (%(max_grounding)s::float8 IS NULL
               OR vr.grounding_score <= %(max_grounding)s::float8)
          AND (%(after_created)s::timestamptz IS NULL
               OR (vr.created_at, vr.id)
                  < (%(after_created)s::timestamptz, %(after_id)s::ulid))
        ORDER BY {order_by}
        LIMIT %(limit)s
        """,
        {
            "doc": document_id,
            "status": status,
            "limit": limit,
            "min_grounding": min_grounding,
            "max_grounding": max_grounding,
            "after_created": cursor["created_at"] if cursor else None,
            "after_id": cursor["id"] if cursor else None,
        },
    )
    rows = await cur.fetchall()
    for row in rows:
        row["cursor"] = encode_cursor("visual-review", created_at=row["created_at"], id=row["id"])
    return rows
