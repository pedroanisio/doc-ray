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


async def retract_visual_caption(
    conn: _AConn,
    *,
    caption_id: str,
    principal_id: str,
    reason: str,
) -> int:
    """Retract one committed caption from the reader, recording who and why.

    With captions published on write (BL-201), the retraction — not the approval
    — is the recorded human verification event under PALS's Law. It therefore
    carries at least what approval carried: the deciding principal, the moment,
    and a reason, so a pattern of bad captions is visible in aggregate rather
    than only as a shrinking count.

    The row is never deleted: it is the audit trail for a paid-for model call and
    for the human decision about it.

    Args:
        conn: Live async connection able to write ``visual_caption``.
        caption_id: The committed caption to retract.
        principal_id: The retracting principal — attribution is mandatory.
        reason: Why it is wrong; must be non-empty.

    Returns:
        The number of rows retracted: 0 when the caption does not exist or was
        not committed, so a caller cannot read a no-op as a success.

    Raises:
        ValueError: ``principal_id`` or ``reason`` is blank.
    """
    if not (principal_id or "").strip():
        raise ValueError("a retraction requires a principal; anonymous retraction is refused")
    if not (reason or "").strip():
        raise ValueError("a retraction requires a reason")
    cur = await conn.execute(
        """
        UPDATE visual_caption
        SET status = %(status)s,
            retracted_at = now(),
            retracted_by = %(principal)s::uuid,
            retraction_reason = %(reason)s
        WHERE id = %(id)s::ulid AND status = 'committed'
        RETURNING 1 AS retracted
        """,
        {
            "id": caption_id,
            "status": "rejected",
            "principal": principal_id,
            "reason": reason.strip(),
        },
    )
    row = await cur.fetchone()
    if row is None:
        return 0
    # This connection carries a dict row factory (see `Row` above), so the
    # column must be read by NAME. Indexing it positionally raises KeyError(0),
    # which surfaced to the client as the GraphQL error message "0".
    return int(row["retracted"] if isinstance(row, dict) else row[0])
