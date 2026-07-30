"""BL-201: retracting a caption is the human verification event, so it is audited.

Approval used to be the recorded human decision. With captions published on write
the recorded decision is the retraction, and it has to carry at least as much as
approval did: who, when, and why. A retraction that merely hid the row would make
the new flow's audit trail weaker than the one it replaced, which is the failure
mode this file exists to prevent.
"""

from __future__ import annotations

from typing import Any

import pytest

from doc_ray.adapters import persistence_visual_write as writer


class _Conn:
    """psycopg-shaped async double capturing statements and parameters."""

    def __init__(self, rowcount: int = 1) -> None:
        self.rowcount = rowcount
        self.calls: list[tuple[str, Any]] = []

    async def execute(self, sql: str, params: Any = None) -> _Conn:
        self.calls.append((sql, params))
        return self

    async def fetchone(self) -> dict[str, int] | None:
        """Dict-shaped, matching the API's row factory.

        A tuple-shaped double hid a real defect: the adapter indexed the row
        positionally, which raises KeyError(0) against a dict row and reached the
        client as the GraphQL error message "0".
        """
        return {"retracted": self.rowcount} if self.rowcount else None

    def transaction(self) -> _Conn:
        return self

    async def __aenter__(self) -> _Conn:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    @property
    def sql(self) -> str:
        return " ".join(sql for sql, _ in self.calls)


@pytest.mark.asyncio
async def test_retraction_records_who_and_when() -> None:
    conn = _Conn()
    await writer.retract_visual_caption(
        conn, caption_id="cap-1", principal_id="prin-1", reason="wrong object"
    )
    assert "retracted_by" in conn.sql
    assert "retracted_at" in conn.sql


@pytest.mark.asyncio
async def test_retraction_records_the_reason() -> None:
    """A pattern of bad captions is only visible if reasons are kept."""
    conn = _Conn()
    await writer.retract_visual_caption(
        conn, caption_id="cap-1", principal_id="prin-1", reason="describes the wrong figure"
    )
    assert "retraction_reason" in conn.sql
    params = conn.calls[0][1]
    assert params["reason"] == "describes the wrong figure"


@pytest.mark.asyncio
async def test_a_retracted_caption_leaves_the_reader() -> None:
    """Retraction must change status, not only stamp the audit columns."""
    conn = _Conn()
    await writer.retract_visual_caption(conn, caption_id="c", principal_id="p", reason="r")
    assert "status" in conn.sql
    assert conn.calls[0][1]["status"] == "rejected"


@pytest.mark.asyncio
async def test_retraction_never_deletes_the_row() -> None:
    """The row is the audit trail for a paid-for API call and a human decision."""
    conn = _Conn()
    await writer.retract_visual_caption(conn, caption_id="c", principal_id="p", reason="r")
    assert "DELETE" not in conn.sql.upper()


@pytest.mark.asyncio
async def test_retracting_an_unknown_caption_reports_zero_rather_than_claiming_success() -> None:
    conn = _Conn(rowcount=0)
    retracted = await writer.retract_visual_caption(
        conn, caption_id="missing", principal_id="p", reason="r"
    )
    assert retracted == 0


@pytest.mark.asyncio
async def test_an_empty_reason_is_refused() -> None:
    """'Why' is the part that makes retractions useful in aggregate."""
    with pytest.raises(ValueError, match="reason"):
        await writer.retract_visual_caption(_Conn(), caption_id="c", principal_id="p", reason="  ")


@pytest.mark.asyncio
async def test_an_anonymous_retraction_is_refused() -> None:
    """Attribution is the whole point of moving the decision to the reader."""
    with pytest.raises(ValueError, match="principal"):
        await writer.retract_visual_caption(_Conn(), caption_id="c", principal_id="", reason="r")


@pytest.mark.asyncio
async def test_only_a_committed_caption_can_be_retracted() -> None:
    """Retracting an already-rejected row would overwrite an earlier decision."""
    conn = _Conn()
    await writer.retract_visual_caption(conn, caption_id="c", principal_id="p", reason="r")
    assert "status = 'committed'" in conn.sql


def test_the_retract_mutation_only_uses_allowed_audit_outcomes() -> None:
    """An invalid outcome fails at runtime, not at import — so pin it here.

    `outcome="noop"` shipped and surfaced to the client as
    `invalid audit outcome: noop` on any retraction that matched nothing, because
    the allowlist is checked inside the audit writer.
    """
    import inspect
    import re

    from doc_ray.entrypoints.api.mutation_visual_review import VisualReviewMutations
    from doc_ray.entrypoints.api.security_audit import _ALLOWED_OUTCOMES

    source = inspect.getsource(VisualReviewMutations)
    used = set(re.findall(r'outcome="([a-z_]+)"', source))
    assert used, "no audit outcome found in the mutation source"
    assert used <= _ALLOWED_OUTCOMES, f"disallowed audit outcomes: {used - _ALLOWED_OUTCOMES}"
