from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from doc_ray.adapters.abac import require_document_write_policy

_ROOT = Path(__file__).resolve().parents[3]
_MIGRATION = _ROOT / "db" / "migrations" / "064_abac_sensitive_document_policy.sql"


class _Cursor:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    async def fetchone(self) -> dict[str, Any] | None:
        return self.row


class _Conn:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, params: tuple[Any, ...]) -> _Cursor:
        self.calls.append((query, params))
        return _Cursor(self.row)


@pytest.mark.asyncio
async def test_require_document_write_policy_denies_sensitive_document_without_abac_grant() -> None:
    conn = _Conn({"ok": False, "reason": "sensitive_document_requires_explicit_grant"})

    with pytest.raises(PermissionError, match="sensitive document"):
        await require_document_write_policy(conn, "00000000-0000-0000-0000-000000000123")

    query, params = conn.calls[0]
    assert "docray_abac_document_write_allowed" in query
    assert params == ("00000000-0000-0000-0000-000000000123",)


@pytest.mark.asyncio
async def test_require_document_write_policy_allows_when_abac_helper_allows() -> None:
    conn = _Conn({"ok": True, "reason": "allowed"})

    await require_document_write_policy(conn, "00000000-0000-0000-0000-000000000123")


def test_sensitive_document_abac_migration_seeds_permission_and_helper() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")

    assert "INSERT INTO permissions (action) VALUES ('sensitive')" in sql
    assert "CREATE OR REPLACE FUNCTION docray_audit_tenant_for_principal" in sql
    assert "CREATE OR REPLACE FUNCTION docray_abac_document_write_allowed" in sql
    assert "document_metadata" in sql
    assert "sensitivity" in sql
    assert "GRANT EXECUTE ON FUNCTION docray_current_principal(), docray_current_tenant()" in sql
    assert (
        "GRANT EXECUTE ON FUNCTION docray_audit_tenant_for_principal(uuid) TO docray_client" in sql
    )
    assert (
        "GRANT EXECUTE ON FUNCTION docray_abac_document_write_allowed(uuid) TO docray_client" in sql
    )
