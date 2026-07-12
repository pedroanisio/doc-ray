"""GraphQL types for the security audit-event surface."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

import strawberry
from strawberry.scalars import JSON

Row = dict[str, Any]


@strawberry.type
class SecurityAuditEvent:
    """One security audit event visible to a tenant admin."""

    id: strawberry.ID
    occurred_at: datetime
    tenant_id: strawberry.ID | None
    actor_principal_id: strawberry.ID | None
    actor_subject: str | None
    action: str
    target_type: str
    target_id: str | None
    outcome: str
    source_ip: str | None
    request_id: str | None
    details: JSON
    cursor: str

    @classmethod
    def from_row(cls, row: Row) -> SecurityAuditEvent:
        """Build a ``SecurityAuditEvent`` from an audit read row."""
        tenant_id = row.get("tenant_id")
        actor_principal_id = row.get("actor_principal_id")
        return cls(
            id=strawberry.ID(str(row["id"])),
            occurred_at=row["occurred_at"],
            tenant_id=strawberry.ID(str(tenant_id)) if tenant_id is not None else None,
            actor_principal_id=(
                strawberry.ID(str(actor_principal_id)) if actor_principal_id is not None else None
            ),
            actor_subject=row.get("actor_subject"),
            action=row["action"],
            target_type=row["target_type"],
            target_id=row.get("target_id"),
            outcome=row["outcome"],
            source_ip=row.get("source_ip"),
            request_id=row.get("request_id"),
            details=cast(JSON, row.get("details") or {}),
            cursor=row.get("cursor") or str(row["id"]),
        )
