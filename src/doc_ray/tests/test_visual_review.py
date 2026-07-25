"""Unit contracts for the Tier-3 visual-caption review API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import strawberry

from doc_ray.adapters.persistence_visual_write import decide_visual_captions
from doc_ray.entrypoints.api.visual_types import VisualReviewItem


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def fetchall(self) -> list[dict[str, Any]]:
        return self.rows


class _Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _Connection:
    def __init__(self, responses: list[list[dict[str, Any]]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, Any]] = []
        self.transactions = 0

    def transaction(self) -> _Transaction:
        self.transactions += 1
        return _Transaction()

    async def execute(self, sql: str, params: Any) -> _Cursor:
        self.calls.append((sql, params))
        return _Cursor(self.responses.pop(0) if self.responses else [])


def test_visual_review_item_maps_proposal_and_cursor() -> None:
    created = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)
    updated = datetime(2026, 7, 25, 12, 5, tzinfo=UTC)
    row = {
        "id": "01REVIEW",
        "document_image_id": "01IMAGE",
        "document_id": "00000000-0000-0000-0000-000000000001",
        "proposed": {"caption": "A grounded diagram", "analysis": {"kind": "chart"}},
        "grounding_score": 0.72,
        "grounding_evidence": {"matched_terms": ["diagram"]},
        "status": "pending",
        "reviewer": None,
        "created_at": created,
        "updated_at": updated,
        "page_number": 3,
        "object_key": "document-images/example/p3.png",
        "cursor": "cursor-1",
    }

    item = VisualReviewItem.from_row(row)

    assert item.id == strawberry.ID("01REVIEW")
    assert item.document_image_id == strawberry.ID("01IMAGE")
    assert item.proposed["caption"] == "A grounded diagram"
    assert item.grounding_evidence == {"matched_terms": ["diagram"]}
    assert item.page_number == 3
    assert item.object_key.endswith("p3.png")
    assert item.created_at == created
    assert item.updated_at == updated
    assert item.cursor == "cursor-1"


@pytest.mark.asyncio
async def test_decide_visual_captions_approves_transactionally() -> None:
    conn = _Connection(
        [
            [{"id": "01REVIEW", "caption_id": "01CAPTION"}],
            [],
            [{"id": "01REVIEW"}],
        ]
    )

    decided = await decide_visual_captions(
        conn,
        approve_ids=["01REVIEW"],
        reject_ids=[],
        reviewer_id="00000000-0000-0000-0000-000000000001",
    )

    assert decided == 1
    assert conn.transactions == 1
    assert any("status = 'committed'" in sql for sql, _ in conn.calls)
    assert any("status = 'approved'" in sql for sql, _ in conn.calls)


@pytest.mark.asyncio
async def test_decide_visual_captions_rejects_shadow_and_queue_row() -> None:
    conn = _Connection([[], [{"id": "01REVIEW"}]])

    decided = await decide_visual_captions(
        conn,
        approve_ids=[],
        reject_ids=["01REVIEW"],
        reviewer_id="00000000-0000-0000-0000-000000000001",
    )

    assert decided == 1
    assert any("status = 'rejected'" in sql for sql, _ in conn.calls)


@pytest.mark.asyncio
async def test_decide_visual_captions_empty_is_noop_and_overlap_is_rejected() -> None:
    conn = _Connection([])
    assert (
        await decide_visual_captions(
            conn,
            approve_ids=[],
            reject_ids=[],
            reviewer_id="00000000-0000-0000-0000-000000000001",
        )
        == 0
    )
    assert conn.transactions == 0
    with pytest.raises(ValueError, match="both approved and rejected"):
        await decide_visual_captions(
            conn,
            approve_ids=["01REVIEW"],
            reject_ids=["01REVIEW"],
            reviewer_id="00000000-0000-0000-0000-000000000001",
        )
