"""Tenant-admin audit search surface tests (BL-075)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from doc_ray.adapters import security_audit_read
from doc_ray.entrypoints.api.query import Query
from doc_ray.entrypoints.api.security import Principal

_ROOT = Path(__file__).resolve().parents[3]


class _FakeCursor:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    async def fetchone(self) -> dict[str, object] | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[dict[str, object]]:
        return self._rows


class _FakeConn:
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self._rows = rows or []

    async def execute(self, query: str, params: tuple[object, ...] = ()) -> _FakeCursor:
        self.calls.append((query, params))
        if "docray_is_admin" in query:
            return _FakeCursor([{"ok": True}])
        return _FakeCursor(self._rows)


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": uuid4(),
        "occurred_at": datetime(2026, 7, 12, 12, 0, tzinfo=UTC),
        "tenant_id": uuid4(),
        "actor_principal_id": uuid4(),
        "actor_subject": "oidc:user-123",
        "action": "graphql.document.read",
        "target_type": "document",
        "target_id": "doc-1",
        "outcome": "success",
        "source_ip": "203.0.113.10",
        "request_id": "req-1",
        "details": {"field": "document"},
        "cursor": "cursor-1",
    }
    row.update(overrides)
    return row


def test_audit_events_admin_read_migration_is_tenant_scoped_and_select_only() -> None:
    migration = (_ROOT / "db" / "migrations" / "063_audit_events_admin_read.sql").read_text(
        encoding="utf-8"
    )
    compact = " ".join(migration.split())

    assert "ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY" in compact
    assert "CREATE POLICY p_audit_admin_select ON audit_events FOR SELECT" in compact
    assert "tenant_id = docray_current_tenant()" in compact
    assert "docray_is_admin()" in compact
    assert "CREATE POLICY p_audit_insert ON audit_events FOR INSERT" in compact
    assert "GRANT SELECT ON audit_events TO docray_client" in compact
    assert "GRANT UPDATE" not in compact
    assert "GRANT DELETE" not in compact
    assert "INSERT INTO schema_migrations (filename) VALUES ('063_audit_events_admin_read.sql')" in (
        migration
    )


def test_list_audit_events_applies_all_filters_and_limit() -> None:
    conn = _FakeConn([_row()])
    after = "2026-07-12T00:00:00+00:00"
    before = "2026-07-13T00:00:00+00:00"

    async def check() -> None:
        rows = await security_audit_read.list_audit_events(
            conn,  # type: ignore[arg-type]
            actor="oidc:user-123",
            action="graphql.document.read",
            target_type="document",
            target_id="doc-1",
            occurred_after=after,
            occurred_before=before,
            source_ip="203.0.113.10",
            limit=25,
            after="cursor-0",
        )
        assert len(rows) == 1

    asyncio.run(check())

    query, params = conn.calls[0]
    assert "FROM audit_events" in query
    assert "actor_principal_id::text = %s OR actor_subject = %s" in query
    assert "action = %s" in query
    assert "target_type = %s" in query
    assert "target_id = %s" in query
    assert "occurred_at >= %s::timestamptz" in query
    assert "occurred_at < %s::timestamptz" in query
    assert "source_ip = %s" in query
    assert "id::text > %s" in query
    assert "LIMIT %s" in query
    assert params[-1] == 25


def test_graphql_audit_events_requires_admin_and_maps_rows() -> None:
    conn = _FakeConn([_row()])
    principal = Principal("oidc:admin", str(uuid4()), frozenset({"read", "write"}))

    async def check() -> None:
        info = SimpleNamespace(
            context=SimpleNamespace(
                conn=conn,
                principal=principal,
                page_size=lambda requested: min(requested, 100),
            )
        )
        result = await Query().audit_events(  # type: ignore[arg-type]
            info,
            actor="oidc:user-123",
            action="graphql.document.read",
            target_type="document",
            target_id="doc-1",
            source_ip="203.0.113.10",
            limit=10,
        )
        assert result[0].action == "graphql.document.read"
        assert result[0].source_ip == "203.0.113.10"

    asyncio.run(check())

    assert any("SELECT docray_is_admin()" in query for query, _params in conn.calls)
