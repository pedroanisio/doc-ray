"""Security audit-log schema and writer tests (BL-074)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from doc_ray.entrypoints.api.query import Query
from doc_ray.entrypoints.api.security import Principal
from doc_ray.entrypoints.api.security_audit import record_audit_event

_ROOT = Path(__file__).resolve().parents[3]


class _FakeCursor:
    async def fetchone(self) -> dict[str, object] | None:
        return None


class _FakeConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, query: str, params: tuple[object, ...] = ()) -> _FakeCursor:
        self.calls.append((query, params))
        return _FakeCursor()


def test_security_audit_migration_defines_insert_only_append_log() -> None:
    migration = (_ROOT / "db" / "migrations" / "062_security_audit_events.sql").read_text(
        encoding="utf-8"
    )
    compact = " ".join(migration.split())

    assert "CREATE TABLE IF NOT EXISTS audit_events" in compact
    assert "actor_principal_id uuid REFERENCES principals(id) ON DELETE SET NULL" in compact
    assert "actor_subject text" in compact
    assert "source_ip text" in compact
    assert "CREATE INDEX IF NOT EXISTS idx_audit_events_actor" in compact
    assert "GRANT INSERT ON audit_events TO docray_client" in compact
    assert "REVOKE SELECT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON audit_events" in (
        compact
    )
    assert "CREATE OR REPLACE FUNCTION docray_prevent_audit_event_mutation()" in compact
    assert "BEFORE UPDATE OR DELETE ON audit_events" in compact
    assert "INSERT INTO schema_migrations (filename) VALUES ('062_security_audit_events.sql')" in (
        migration
    )


def test_record_audit_event_inserts_actor_action_target_and_ip() -> None:
    conn = _FakeConn()
    principal_id = str(uuid4())
    principal = Principal(
        subject="oidc:user-123",
        principal_id=principal_id,
        scopes=frozenset({"read", "write"}),
    )

    async def check() -> None:
        await record_audit_event(
            conn,  # type: ignore[arg-type]
            principal=principal,
            action="graphql.document.read",
            target_type="document",
            target_id="doc-1",
            outcome="success",
            source_ip="203.0.113.10",
            request_id="req-1",
            details={"field": "document"},
        )

    asyncio.run(check())

    assert len(conn.calls) == 1
    query, params = conn.calls[0]
    assert "INSERT INTO audit_events" in query
    assert "docray_audit_tenant_for_principal" in query
    assert "FROM principals WHERE id" not in query
    assert params[:8] == (
        principal_id,
        "oidc:user-123",
        "graphql.document.read",
        "document",
        "doc-1",
        "success",
        "203.0.113.10",
        "req-1",
    )


def test_document_query_records_sensitive_read_audit_event(monkeypatch) -> None:
    conn = _FakeConn()
    principal = Principal("oidc:reader", str(uuid4()), frozenset({"read"}))

    async def fake_get_document(_conn: object, document_id: str) -> dict[str, object]:
        return {
            "id": document_id,
            "title": "Audited",
            "doc_type": "txt",
            "source_uri": "s3://documents/a.txt",
            "content_sha256": "00",
            "created_at": None,
            "status": "extracted",
            "page_count": None,
            "lifecycle_state": "active",
            "archived_at": None,
            "deleted_at": None,
            "mime_type": "text/plain",
            "original_filename": "a.txt",
            "byte_size": 1,
        }

    monkeypatch.setattr("doc_ray.entrypoints.api.query.read.get_document", fake_get_document)

    async def check() -> None:
        info = SimpleNamespace(context=SimpleNamespace(conn=conn, principal=principal))
        result = await Query().document(info, uuid4())  # type: ignore[arg-type]
        assert result is not None

    asyncio.run(check())

    assert any("INSERT INTO audit_events" in query for query, _params in conn.calls)
    audit_params = [params for query, params in conn.calls if "INSERT INTO audit_events" in query]
    assert audit_params[0][2:6] == (
        "graphql.document.read",
        "document",
        str(audit_params[0][4]),
        "success",
    )
