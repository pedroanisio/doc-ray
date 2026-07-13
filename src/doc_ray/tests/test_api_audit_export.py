"""Audit export endpoint helper tests (BL-075)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from doc_ray.config import Config
from doc_ray.entrypoints.api.app import (
    _audit_events_csv,
    _audit_events_json,
    _export_audit_events_endpoint,
    create_app,
)
from doc_ray.entrypoints.api.security import Principal


def _event() -> dict[str, object]:
    return {
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


def test_audit_events_json_export_serializes_rows() -> None:
    body = _audit_events_json([_event()])
    payload = json.loads(body)

    assert payload["events"][0]["actor_subject"] == "oidc:user-123"
    assert payload["events"][0]["details"] == {"field": "document"}
    assert payload["events"][0]["occurred_at"] == "2026-07-12T12:00:00+00:00"


def test_audit_events_csv_export_has_stable_headers_and_json_details() -> None:
    body = _audit_events_csv([_event()])
    lines = body.splitlines()

    assert lines[0].startswith("id,occurred_at,tenant_id,actor_principal_id,actor_subject")
    assert "graphql.document.read" in lines[1]
    assert '""field"": ""document""' in lines[1]


def test_create_app_registers_audit_export_endpoint() -> None:
    app = create_app(
        Config(
            database_url="postgresql://example",
            s3_endpoint="http://localhost:9000",
            s3_access_key="minioadmin",
            s3_secret_key="minioadmin",
            s3_bucket="documents",
            s3_region="us-east-1",
        )
    )

    assert "/audit/events/export" in {route.path for route in app.routes if hasattr(route, "path")}


def test_audit_export_endpoint_maps_non_admin_to_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeAuth:
        async def authenticate(self, _request: object) -> Principal:
            return Principal("oidc:user", str(uuid4()), frozenset({"read"}))

    class FakePool:
        def connection(self) -> object:
            return self

        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_exc: object) -> None:
            return None

    async def noop(*_args: object, **_kwargs: object) -> None:
        return None

    async def deny_admin(_conn: object) -> None:
        raise PermissionError("audit events require an admin principal")

    monkeypatch.setattr("doc_ray.entrypoints.api.audit_export.apply_principal", noop)
    monkeypatch.setattr("doc_ray.entrypoints.api.audit_export.reset_principal", noop)
    monkeypatch.setattr(
        "doc_ray.entrypoints.api.audit_export.security_audit_read.require_audit_admin",
        deny_admin,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(authenticator=FakeAuth(), pool=FakePool()))
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_export_audit_events_endpoint(request))  # type: ignore[arg-type]

    assert exc_info.value.status_code == 403
