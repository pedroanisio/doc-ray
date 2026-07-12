"""SCIM provisioning tests (BL-068)."""

from __future__ import annotations

from pathlib import Path

import _itdb
import httpx
import pytest

from doc_ray.config import Config
from doc_ray.entrypoints.api.app import create_app
from doc_ray.entrypoints.api.scim import SCIMRepository, register_scim_routes

_ROOT = Path(__file__).resolve().parents[3]
_SCIM_MIGRATION = _ROOT / "db" / "migrations" / "065_scim_provisioning.sql"


def _apply_scim_migration(conn) -> None:
    conn.execute(_SCIM_MIGRATION.read_text(encoding="utf-8"))


def test_config_reads_scim_token_map(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DOCRAY_SCIM_TOKENS",
        "scim-secret=00000000-0000-0000-0000-000000000111",
    )

    cfg = Config.from_env()

    assert cfg.scim_tokens == {
        "scim-secret": "00000000-0000-0000-0000-000000000111",
    }


def test_scim_migration_marks_managed_principals_and_blocks_manual_role_edits(pg) -> None:
    conn, _created = pg
    _apply_scim_migration(conn)
    suffix = _itdb.unique_sha()[:8]
    tenant_id = conn.execute(
        "INSERT INTO tenants (slug, display_name) VALUES (%s, %s) RETURNING id",
        (f"scim-tenant-{suffix}", "SCIM Tenant"),
    ).fetchone()["id"]
    role_id = conn.execute("SELECT id FROM roles WHERE name = 'curator'").fetchone()["id"]
    principal_id = conn.execute(
        "INSERT INTO principals "
        "(kind, display_name, tenant_id, managed_by, managed_source_id, active) "
        "VALUES ('human', %s, %s, 'scim', 'user-1', true) "
        "RETURNING id",
        (f"SCIM User {suffix}", tenant_id),
    ).fetchone()["id"]

    with pytest.raises(Exception, match="SCIM-managed principal roles"):
        conn.execute(
            "INSERT INTO principal_roles (principal_id, role_id) VALUES (%s, %s)",
            (principal_id, role_id),
        )

    conn.execute("SELECT set_config('app.scim_sync', 'on', false)")
    conn.execute(
        "INSERT INTO principal_roles (principal_id, role_id) VALUES (%s, %s)",
        (principal_id, role_id),
    )
    row = conn.execute(
        "SELECT count(*) AS n FROM principal_roles WHERE principal_id = %s",
        (principal_id,),
    ).fetchone()

    assert row["n"] == 1


def test_scim_repository_deactivation_revokes_roles_and_document_grants(pg) -> None:
    conn, created = pg
    _apply_scim_migration(conn)
    suffix = _itdb.unique_sha()[:8]
    tenant_id = conn.execute(
        "INSERT INTO tenants (slug, display_name) VALUES (%s, %s) RETURNING id",
        (f"scim-deprovision-{suffix}", "SCIM Deprovision Tenant"),
    ).fetchone()["id"]
    doc_id = _itdb.make_doc(conn, f"SCIM deprovision document {suffix}.")
    created.append(doc_id)
    repo = SCIMRepository(conn)

    user = repo.upsert_user(
        tenant_id=str(tenant_id),
        source_id="user-1",
        user_name="user@example.com",
        display_name=f"SCIM User {suffix}",
        active=True,
    )
    conn.execute("SELECT set_config('app.scim_sync', 'on', false)")
    role_id = conn.execute("SELECT id FROM roles WHERE name = 'curator'").fetchone()["id"]
    conn.execute(
        "INSERT INTO principal_roles (principal_id, role_id) VALUES (%s, %s)",
        (user.id, role_id),
    )
    conn.execute(
        "INSERT INTO document_acl (document_id, principal_id, permission) "
        "VALUES (%s, %s, 'read')",
        (doc_id, user.id),
    )

    deactivated = repo.deactivate_user(tenant_id=str(tenant_id), source_id="user-1")

    assert deactivated.active is False
    assert conn.execute(
        "SELECT count(*) AS n FROM principal_roles WHERE principal_id = %s",
        (user.id,),
    ).fetchone()["n"] == 0
    assert conn.execute(
        "SELECT count(*) AS n FROM document_acl WHERE principal_id = %s",
        (user.id,),
    ).fetchone()["n"] == 0


def test_scim_endpoint_creates_tenant_scoped_user_and_group(pg) -> None:
    conn, _created = pg
    _apply_scim_migration(conn)
    suffix = _itdb.unique_sha()[:8]
    tenant_id = conn.execute(
        "INSERT INTO tenants (slug, display_name) VALUES (%s, %s) RETURNING id",
        (f"scim-route-{suffix}", "SCIM Route Tenant"),
    ).fetchone()["id"]
    cfg = Config(
        database_url="postgresql://example",
        s3_endpoint="http://localhost:9000",
        s3_access_key="minioadmin",
        s3_secret_key="minioadmin",
        s3_bucket="documents",
        s3_region="us-east-1",
        scim_tokens={"scim-secret": str(tenant_id)},
    )
    app = create_app(cfg)
    app.state.pool = _Pool(conn)
    register_scim_routes(app, cfg)

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            user_response = await client.post(
                f"/scim/v2/{tenant_id}/Users",
                headers={"authorization": "Bearer scim-secret"},
                json={
                    "id": "idp-user-1",
                    "userName": f"user-{suffix}@example.com",
                    "displayName": f"SCIM User {suffix}",
                    "active": True,
                },
            )
            group_response = await client.post(
                f"/scim/v2/{tenant_id}/Groups",
                headers={"authorization": "Bearer scim-secret"},
                json={
                    "id": "idp-group-1",
                    "displayName": "Curators",
                    "members": [{"value": "idp-user-1"}],
                },
            )

        assert user_response.status_code == 201
        assert user_response.json()["userName"] == f"user-{suffix}@example.com"
        assert group_response.status_code == 201
        assert group_response.json()["members"] == [{"value": "idp-user-1"}]

    import asyncio

    asyncio.run(exercise())


class _Pool:
    def __init__(self, conn) -> None:
        self._conn = conn

    def connection(self) -> _PoolConnection:
        return _PoolConnection(self._conn)


class _PoolConnection:
    def __init__(self, conn) -> None:
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None
