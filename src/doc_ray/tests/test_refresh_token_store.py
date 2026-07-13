"""Durable refresh-token store: wiring + Postgres rotation semantics.

Regression for the audit MAJOR: migration 074 created auth_refresh_tokens but
no code ever touched it — refresh tokens lived only in process memory, so any
API restart revoked every session. The DB-backed tests use the live-DB guard
(skip locally, run in the isolated Docker stack / CI) because rotation
semantics are enforced with row locks.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from doc_ray.adapters.refresh_token_store_postgres import PostgresRefreshTokenStore
from doc_ray.entrypoints.api.security import Principal, RefreshTokenRecord


def test_create_app_wires_a_durable_refresh_token_store(monkeypatch) -> None:
    """The API must default to the Postgres store, not process memory."""
    import doc_ray.entrypoints.api.app as api_app
    from doc_ray.config import Config
    from doc_ray.entrypoints.api.app import create_app

    class _ClosedPool:
        closed = True
        _opened = False

        async def open(self) -> None:
            return None

        async def close(self) -> None:
            return None

    monkeypatch.setattr(api_app, "AsyncConnectionPool", lambda *a, **k: _ClosedPool())
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

    assert isinstance(app.state.refresh_token_store, PostgresRefreshTokenStore)


def _store_and_principal(pg):
    conn, _created = pg
    row = conn.execute(
        "INSERT INTO principals (kind, display_name) VALUES ('service', %s) RETURNING id",
        (f"refresh-store-test-{uuid.uuid4()}",),
    ).fetchone()
    principal_id = str(row["id"])
    import os

    store = PostgresRefreshTokenStore(os.environ.get("DATABASE_URL", ""))
    principal = Principal(
        subject="local:alice@example.com",
        principal_id=principal_id,
        scopes=frozenset({"read"}),
    )
    return conn, store, principal, principal_id


def _record(principal, token_id: str, family_id: str, *, expires_in: int = 3600):
    return RefreshTokenRecord(
        token_id=token_id,
        family_id=family_id,
        principal=principal,
        expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
    )


def test_postgres_store_round_trips_and_rotates(pg) -> None:
    conn, store, principal, principal_id = _store_and_principal(pg)
    token_id, family_id = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        store.remember(_record(principal, token_id, family_id))

        record = store.consume(token_id, now=datetime.now(UTC))

        assert record.principal.principal_id == principal_id
        assert record.principal.subject == "local:alice@example.com"
        assert record.principal.scopes == frozenset({"read"})
        assert record.family_id == family_id
        row = conn.execute(
            "SELECT status FROM auth_refresh_tokens WHERE token_id = %s", (token_id,)
        ).fetchone()
        assert row["status"] == "rotated"
    finally:
        conn.execute("DELETE FROM principals WHERE id = %s", (principal_id,))


def test_postgres_store_reuse_revokes_the_whole_family(pg) -> None:
    conn, store, principal, principal_id = _store_and_principal(pg)
    family_id = str(uuid.uuid4())
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        store.remember(_record(principal, first, family_id))
        store.consume(first, now=datetime.now(UTC))
        store.remember(_record(principal, second, family_id))

        with pytest.raises(ValueError, match="reused"):
            store.consume(first, now=datetime.now(UTC))

        # Reuse detection must burn the successor token too.
        with pytest.raises(ValueError):
            store.consume(second, now=datetime.now(UTC))
        row = conn.execute(
            "SELECT status FROM auth_refresh_tokens WHERE token_id = %s", (second,)
        ).fetchone()
        assert row["status"] == "revoked"
    finally:
        conn.execute("DELETE FROM principals WHERE id = %s", (principal_id,))


def test_postgres_store_expires_and_rejects_unknown(pg) -> None:
    conn, store, principal, principal_id = _store_and_principal(pg)
    token_id, family_id = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        store.remember(_record(principal, token_id, family_id, expires_in=-5))

        with pytest.raises(ValueError, match="expired"):
            store.consume(token_id, now=datetime.now(UTC))
        row = conn.execute(
            "SELECT status FROM auth_refresh_tokens WHERE token_id = %s", (token_id,)
        ).fetchone()
        assert row["status"] == "expired"

        with pytest.raises(ValueError, match="unknown"):
            store.consume(str(uuid.uuid4()), now=datetime.now(UTC))
    finally:
        conn.execute("DELETE FROM principals WHERE id = %s", (principal_id,))
