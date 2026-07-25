"""Transactional write adapter for human decisions on Tier-3 visual captions."""

from __future__ import annotations

from typing import Any

import psycopg

Row = dict[str, Any]
_AConn = psycopg.AsyncConnection[Row]


async def decide_visual_captions(
    conn: _AConn,
    *,
    approve_ids: list[str],
    reject_ids: list[str],
    reviewer_id: str,
) -> int:
    """Atomically commit approved caption proposals and reject the remainder.

    Only pending, RLS-visible rows transition. An approval requires the shadow
    ``visual_caption(status='review')`` row written with the proposal; its
    absence aborts and rolls back the whole batch rather than recording a false
    approval with no trusted caption.
    """
    approve_ids = list(dict.fromkeys(approve_ids))
    reject_ids = list(dict.fromkeys(reject_ids))
    if set(approve_ids) & set(reject_ids):
        raise ValueError("a visual review id cannot be both approved and rejected")
    if not approve_ids and not reject_ids:
        return 0

    decided = 0
    async with conn.transaction():
        if approve_ids:
            cur = await conn.execute(
                """
                SELECT vr.id, vc.id AS caption_id
                FROM visual_review vr
                LEFT JOIN visual_caption vc
                  ON vc.document_image_id = vr.document_image_id
                 AND vc.status = 'review'
                WHERE vr.id = ANY(%s::ulid[]) AND vr.status = 'pending'
                FOR UPDATE OF vr
                """,
                (approve_ids,),
            )
            rows = await cur.fetchall()
            missing = [str(row["id"]) for row in rows if row["caption_id"] is None]
            if missing:
                raise RuntimeError(
                    "visual review approval has no review-status caption row: " + ", ".join(missing)
                )
            await conn.execute(
                """
                UPDATE visual_caption vc
                SET caption = coalesce(vr.proposed ->> 'caption', vc.caption),
                    analysis = coalesce(vr.proposed -> 'analysis', vc.analysis),
                    grounding_score = vr.grounding_score,
                    grounded_against = vr.grounding_evidence,
                    status = 'committed'
                FROM visual_review vr
                WHERE vr.id = ANY(%s::ulid[]) AND vr.status = 'pending'
                  AND vc.document_image_id = vr.document_image_id
                  AND vc.status = 'review'
                """,
                (approve_ids,),
            )
            cur = await conn.execute(
                """
                UPDATE visual_review
                SET status = 'approved', reviewer = %s::uuid
                WHERE id = ANY(%s::ulid[]) AND status = 'pending'
                RETURNING id
                """,
                (reviewer_id, approve_ids),
            )
            decided += len(await cur.fetchall())

        if reject_ids:
            await conn.execute(
                """
                UPDATE visual_caption vc
                SET status = 'rejected'
                FROM visual_review vr
                WHERE vr.id = ANY(%s::ulid[]) AND vr.status = 'pending'
                  AND vc.document_image_id = vr.document_image_id
                  AND vc.status = 'review'
                """,
                (reject_ids,),
            )
            cur = await conn.execute(
                """
                UPDATE visual_review
                SET status = 'rejected', reviewer = %s::uuid
                WHERE id = ANY(%s::ulid[]) AND status = 'pending'
                RETURNING id
                """,
                (reviewer_id, reject_ids),
            )
            decided += len(await cur.fetchall())

    return decided
