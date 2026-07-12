"""Attribute-based authorization checks layered on top of RBAC."""

from __future__ import annotations

from typing import Any, Protocol


class _Cursor(Protocol):
    async def fetchone(self) -> dict[str, Any] | None:
        """Return one result row."""
        ...


class _AsyncConn(Protocol):
    async def execute(self, query: str, params: tuple[Any, ...] = ()) -> _Cursor:
        """Execute a query and return a cursor."""
        ...


async def require_document_write_policy(conn: _AsyncConn, document_id: str) -> None:
    """Require document-level ABAC policy to allow a write.

    RBAC/document_acl checks happen before this function. This is the
    contextual/resource-attribute layer: the database helper can evaluate
    document metadata, tenant-aware identity state, admin status, and explicit
    grants without exposing raw ACL tables to the app role.
    """
    cur = await conn.execute(
        "SELECT ok, reason FROM docray_abac_document_write_allowed(%s::uuid)",
        (document_id,),
    )
    row = await cur.fetchone()
    if row and row.get("ok"):
        return
    reason = str(row.get("reason") if row else "abac_policy_denied")
    if reason == "sensitive_document_requires_explicit_grant":
        raise PermissionError("sensitive document requires an explicit sensitive grant")
    raise PermissionError(f"document write denied by ABAC policy: {reason}")
