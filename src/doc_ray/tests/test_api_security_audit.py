"""API security audit event tests (BL-074)."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from doc_ray.entrypoints.api.security import Principal
from doc_ray.entrypoints.api.security_audit import record_audit_event_from_pool


class _FakeCursor:
    async def fetchone(self) -> dict[str, object] | None:
        return None


class _FakeAuditConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, query: str, params: tuple[object, ...] = ()) -> _FakeCursor:
        self.calls.append((query, params))
        return _FakeCursor()


class _FakeConnectionContext:
    def __init__(self, conn: _FakeAuditConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeAuditConn:
        return self._conn

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _FakeAuditPool:
    def __init__(self) -> None:
        self.conn = _FakeAuditConn()

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def connection(self) -> _FakeConnectionContext:
        return _FakeConnectionContext(self.conn)


def test_auth_token_events_record_success_and_failure_without_token_values() -> None:
    pool = _FakeAuditPool()
    principal_id = str(uuid4())
    principal = Principal(
        subject="token:static-token",
        principal_id=principal_id,
        scopes=frozenset({"read", "write"}),
    )

    async def check() -> None:
        await record_audit_event_from_pool(
            pool,  # type: ignore[arg-type]
            principal=None,
            action="auth.token.issue",
            target_type="auth",
            target_id=None,
            outcome="failure",
            source_ip="testclient",
            details={"status_code": 401},
        )
        await record_audit_event_from_pool(
            pool,  # type: ignore[arg-type]
            principal=principal,
            action="auth.token.issue",
            target_type="auth",
            target_id=None,
            outcome="success",
            source_ip="testclient",
        )

    asyncio.run(check())
    audit_params = [
        params for query, params in pool.conn.calls if "INSERT INTO audit_events" in query
    ]
    assert [params[5] for params in audit_params] == ["failure", "success"]
    assert {params[2] for params in audit_params} == {"auth.token.issue"}
    assert audit_params[0][0] is None
    assert audit_params[1][0] == principal_id
    serialized_params = repr(audit_params)
    assert "wrong-token" not in serialized_params
    assert "static-token" not in serialized_params
