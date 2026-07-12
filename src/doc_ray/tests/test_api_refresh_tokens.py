"""Refresh-token rotation tests (BL-072)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from doc_ray.config import Config
from doc_ray.entrypoints.api.app import create_app
from doc_ray.entrypoints.api.security import (
    InMemoryRefreshTokenStore,
    Principal,
    issue_refresh_token,
    rotate_refresh_token,
)


def _cfg(principal_id: str) -> Config:
    return Config(
        database_url="postgresql://example",
        s3_endpoint="http://localhost:9000",
        s3_access_key="minioadmin",
        s3_secret_key="minioadmin",
        s3_bucket="documents",
        s3_region="us-east-1",
        api_tokens={"static-token": (principal_id, frozenset({"read", "write"}))},
        api_allow_anonymous=False,
        api_jwt_secret="jwt-signing-secret",
        api_jwt_issuer="doc-ray",
        api_jwt_audience="docray-api",
        api_jwt_ttl_seconds=300,
        api_refresh_token_ttl_seconds=86_400,
    )


def test_config_reads_refresh_token_ttl(monkeypatch):
    monkeypatch.setenv("DOCRAY_API_REFRESH_TOKEN_TTL_SECONDS", "43200")

    assert Config.from_env().api_refresh_token_ttl_seconds == 43_200


def test_token_exchange_returns_access_and_refresh_tokens():
    app = create_app(_cfg(str(uuid4())))

    response = TestClient(app).post("/auth/token", headers={"authorization": "Bearer static-token"})

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 300
    assert body["refresh_expires_in"] == 86_400
    assert isinstance(body["access_token"], str)
    assert isinstance(body["refresh_token"], str)
    assert body["access_token"] != body["refresh_token"]


def test_refresh_endpoint_rotates_presented_refresh_token():
    app = create_app(_cfg(str(uuid4())))
    client = TestClient(app)
    first = client.post("/auth/token", headers={"authorization": "Bearer static-token"}).json()

    response = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})

    assert response.status_code == 200
    second = response.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert second["access_token"] != first["access_token"]
    assert second["refresh_expires_in"] == 86_400


def test_reusing_rotated_refresh_token_revokes_token_family():
    app = create_app(_cfg(str(uuid4())))
    client = TestClient(app)
    first = client.post("/auth/token", headers={"authorization": "Bearer static-token"}).json()
    second = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}).json()

    replay = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    after_replay = client.post("/auth/refresh", json={"refresh_token": second["refresh_token"]})

    assert replay.status_code == 401
    assert replay.json()["detail"] == "Invalid refresh token"
    assert after_replay.status_code == 401
    assert after_replay.json()["detail"] == "Invalid refresh token"


def test_refresh_rotation_rejects_expired_token():
    now = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
    store = InMemoryRefreshTokenStore()
    token = issue_refresh_token(
        Principal("token:static-token", str(uuid4()), frozenset({"read"})),
        secret="jwt-signing-secret",
        issuer="doc-ray",
        audience="docray-api",
        ttl_seconds=60,
        store=store,
        now=now,
    )

    with pytest.raises(ValueError, match="expired"):
        rotate_refresh_token(
            token,
            secret="jwt-signing-secret",
            issuer="doc-ray",
            audience="docray-api",
            ttl_seconds=60,
            access_ttl_seconds=300,
            store=store,
            now=now + timedelta(seconds=61),
        )
