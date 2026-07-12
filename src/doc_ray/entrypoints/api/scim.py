"""Minimal tenant-scoped SCIM 2.0 provisioning surface (BL-068)."""

from __future__ import annotations

from dataclasses import dataclass
from inspect import isawaitable
from types import TracebackType
from typing import Any, Protocol, cast

from fastapi import FastAPI, HTTPException, Request, status

from doc_ray.config import Config


@dataclass(frozen=True)
class SCIMUser:
    """Provisioned SCIM user mapped onto a doc-ray principal."""

    id: str
    source_id: str
    user_name: str
    display_name: str
    tenant_id: str
    active: bool


@dataclass(frozen=True)
class SCIMGroup:
    """Provisioned SCIM group and its IdP member source ids."""

    id: str
    source_id: str
    display_name: str
    tenant_id: str
    active: bool
    members: list[str]


_Row = dict[str, object]


class _Cursor(Protocol):
    def fetchone(self) -> _Row | None:
        ...


class _SyncConnection(Protocol):
    def execute(self, query: str, params: tuple[object, ...] = ...) -> _Cursor:
        ...


class SCIMRepository:
    """Synchronous repository for SCIM-managed users and groups."""

    def __init__(self, conn: _SyncConnection) -> None:
        """Initialize the repository with an open database connection."""
        self._conn = conn

    def upsert_user(
        self,
        *,
        tenant_id: str,
        source_id: str,
        user_name: str,
        display_name: str,
        active: bool,
    ) -> SCIMUser:
        """Create or update a tenant-scoped SCIM user principal."""
        self._conn.execute("SELECT set_config('app.scim_sync', 'on', false)")
        row = self._conn.execute(
            """
            INSERT INTO principals
                (kind, display_name, external_subject, tenant_id, managed_by,
                 managed_source_id, active)
            VALUES
                ('human', %s, %s, %s, 'scim', %s, %s)
            ON CONFLICT (tenant_id, managed_by, managed_source_id)
            WHERE managed_by IS NOT NULL AND managed_source_id IS NOT NULL
            DO UPDATE SET
                display_name = EXCLUDED.display_name,
                external_subject = EXCLUDED.external_subject,
                active = EXCLUDED.active
            RETURNING id, managed_source_id, external_subject, display_name, tenant_id, active
            """,
            (
                display_name,
                _external_subject(tenant_id, source_id),
                tenant_id,
                source_id,
                active,
            ),
        ).fetchone()
        if row is None:  # pragma: no cover - RETURNING makes this unreachable
            raise RuntimeError(f"failed to upsert SCIM user: {source_id}")
        return _user_from_row(row, user_name)

    def deactivate_user(self, *, tenant_id: str, source_id: str) -> SCIMUser:
        """Deactivate a SCIM user and revoke grants/roles the IdP no longer owns."""
        self._conn.execute("SELECT set_config('app.scim_sync', 'on', false)")
        row = self._conn.execute(
            """
            UPDATE principals
               SET active = false
             WHERE tenant_id = %s
               AND managed_by = 'scim'
               AND managed_source_id = %s
            RETURNING id, managed_source_id, external_subject, display_name, tenant_id, active
            """,
            (tenant_id, source_id),
        ).fetchone()
        if row is None:
            raise KeyError(source_id)
        self._conn.execute("DELETE FROM principal_roles WHERE principal_id = %s", (row["id"],))
        self._conn.execute("DELETE FROM document_acl WHERE principal_id = %s", (row["id"],))
        self._conn.execute(
            """
            DELETE FROM scim_group_members
             WHERE principal_id = %s
            """,
            (row["id"],),
        )
        return _user_from_row(row, _user_name_from_external(str(row["external_subject"])))

    def upsert_group(
        self,
        *,
        tenant_id: str,
        source_id: str,
        display_name: str,
        members: list[str],
        active: bool = True,
    ) -> SCIMGroup:
        """Create or update a tenant-scoped SCIM group and its memberships."""
        self._conn.execute("SELECT set_config('app.scim_sync', 'on', false)")
        row = self._conn.execute(
            """
            INSERT INTO scim_groups (tenant_id, source_id, display_name, active)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (tenant_id, source_id) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                active = EXCLUDED.active,
                updated_at = now()
            RETURNING id, tenant_id, source_id, display_name, active
            """,
            (tenant_id, source_id, display_name, active),
        ).fetchone()
        if row is None:  # pragma: no cover - RETURNING makes this unreachable
            raise RuntimeError(f"failed to upsert SCIM group: {source_id}")
        self._conn.execute("DELETE FROM scim_group_members WHERE group_id = %s", (row["id"],))
        for member_source_id in members:
            principal = self._conn.execute(
                """
                SELECT id FROM principals
                 WHERE tenant_id = %s
                   AND managed_by = 'scim'
                   AND managed_source_id = %s
                   AND active
                """,
                (tenant_id, member_source_id),
            ).fetchone()
            if principal is not None:
                self._conn.execute(
                    """
                    INSERT INTO scim_group_members (group_id, principal_id)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (row["id"], principal["id"]),
                )
        return _group_from_row(row, members)


class _AsyncConnection(Protocol):
    def execute(self, query: str, params: tuple[object, ...] = ...) -> object:
        ...


class _AsyncCursor(Protocol):
    def fetchone(self) -> object:
        ...


class _ConnectionContext(Protocol):
    async def __aenter__(self) -> _AsyncConnection:
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        ...


class _PoolLike(Protocol):
    def connection(self) -> _ConnectionContext:
        ...


class AsyncSCIMRepository:
    """Async repository variant used by FastAPI routes."""

    def __init__(self, conn: _AsyncConnection) -> None:
        """Initialize the repository with an open database connection."""
        self._conn = conn

    async def upsert_user(
        self,
        *,
        tenant_id: str,
        source_id: str,
        user_name: str,
        display_name: str,
        active: bool,
    ) -> SCIMUser:
        """Create or update a tenant-scoped SCIM user principal."""
        await self._execute("SELECT set_config('app.scim_sync', 'on', false)")
        cur = await self._execute(
            """
            INSERT INTO principals
                (kind, display_name, external_subject, tenant_id, managed_by,
                 managed_source_id, active)
            VALUES
                ('human', %s, %s, %s, 'scim', %s, %s)
            ON CONFLICT (tenant_id, managed_by, managed_source_id)
            WHERE managed_by IS NOT NULL AND managed_source_id IS NOT NULL
            DO UPDATE SET
                display_name = EXCLUDED.display_name,
                external_subject = EXCLUDED.external_subject,
                active = EXCLUDED.active
            RETURNING id, managed_source_id, external_subject, display_name, tenant_id, active
            """,
            (
                display_name,
                _external_subject(tenant_id, source_id, user_name),
                tenant_id,
                source_id,
                active,
            ),
        )
        row = await self._fetchone(cur)
        if row is None:  # pragma: no cover
            raise RuntimeError(f"failed to upsert SCIM user: {source_id}")
        return _user_from_row(row, user_name)

    async def deactivate_user(self, *, tenant_id: str, source_id: str) -> SCIMUser:
        """Deactivate a SCIM user and revoke grants/roles the IdP no longer owns."""
        await self._execute("SELECT set_config('app.scim_sync', 'on', false)")
        cur = await self._execute(
            """
            UPDATE principals
               SET active = false
             WHERE tenant_id = %s
               AND managed_by = 'scim'
               AND managed_source_id = %s
            RETURNING id, managed_source_id, external_subject, display_name, tenant_id, active
            """,
            (tenant_id, source_id),
        )
        row = await self._fetchone(cur)
        if row is None:
            raise KeyError(source_id)
        await self._execute("DELETE FROM principal_roles WHERE principal_id = %s", (row["id"],))
        await self._execute("DELETE FROM document_acl WHERE principal_id = %s", (row["id"],))
        await self._execute("DELETE FROM scim_group_members WHERE principal_id = %s", (row["id"],))
        return _user_from_row(row, _user_name_from_external(str(row["external_subject"])))

    async def upsert_group(
        self,
        *,
        tenant_id: str,
        source_id: str,
        display_name: str,
        members: list[str],
        active: bool = True,
    ) -> SCIMGroup:
        """Create or update a tenant-scoped SCIM group and its memberships."""
        await self._execute("SELECT set_config('app.scim_sync', 'on', false)")
        cur = await self._execute(
            """
            INSERT INTO scim_groups (tenant_id, source_id, display_name, active)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (tenant_id, source_id) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                active = EXCLUDED.active,
                updated_at = now()
            RETURNING id, tenant_id, source_id, display_name, active
            """,
            (tenant_id, source_id, display_name, active),
        )
        row = await self._fetchone(cur)
        if row is None:  # pragma: no cover
            raise RuntimeError(f"failed to upsert SCIM group: {source_id}")
        await self._execute("DELETE FROM scim_group_members WHERE group_id = %s", (row["id"],))
        for member_source_id in members:
            cur = await self._execute(
                """
                SELECT id FROM principals
                 WHERE tenant_id = %s
                   AND managed_by = 'scim'
                   AND managed_source_id = %s
                   AND active
                """,
                (tenant_id, member_source_id),
            )
            principal = await self._fetchone(cur)
            if principal is not None:
                await self._execute(
                    """
                    INSERT INTO scim_group_members (group_id, principal_id)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (row["id"], principal["id"]),
                )
        return _group_from_row(row, members)

    async def _execute(self, query: str, params: tuple[object, ...] = ()) -> _AsyncCursor:
        result = self._conn.execute(query, params)
        if isawaitable(result):
            result = await result
        return cast(_AsyncCursor, result)

    async def _fetchone(self, cursor: _AsyncCursor) -> _Row | None:
        result = cursor.fetchone()
        if isawaitable(result):
            return cast(_Row | None, await result)
        return cast(_Row | None, result)


def register_scim_routes(app: FastAPI, settings: Config) -> None:
    """Register tenant-scoped SCIM 2.0 Users and Groups endpoints."""

    async def scim_pool(request: Request, tenant_id: str) -> _PoolLike:
        _require_scim_token(request, settings, tenant_id)
        pool = cast(_PoolLike, request.app.state.pool)
        return pool

    @app.post("/scim/v2/{tenant_id}/Users", status_code=status.HTTP_201_CREATED)
    async def create_scim_user(
        tenant_id: str, payload: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        pool = await scim_pool(request, tenant_id)
        user = _scim_user_request(payload)
        async with pool.connection() as conn:
            created = await AsyncSCIMRepository(conn).upsert_user(tenant_id=tenant_id, **user)
        return _scim_user_response(created)

    @app.put("/scim/v2/{tenant_id}/Users/{source_id}")
    async def update_scim_user(
        tenant_id: str, source_id: str, payload: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        pool = await scim_pool(request, tenant_id)
        user = _scim_user_request({**payload, "id": source_id})
        async with pool.connection() as conn:
            updated = await AsyncSCIMRepository(conn).upsert_user(tenant_id=tenant_id, **user)
        return _scim_user_response(updated)

    @app.patch("/scim/v2/{tenant_id}/Users/{source_id}")
    async def patch_scim_user(
        tenant_id: str, source_id: str, payload: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        pool = await scim_pool(request, tenant_id)
        active = _scim_active_from_patch(payload)
        async with pool.connection() as conn:
            repo = AsyncSCIMRepository(conn)
            if active is False:
                try:
                    user = await repo.deactivate_user(tenant_id=tenant_id, source_id=source_id)
                except KeyError as exc:
                    raise HTTPException(status_code=404, detail="SCIM user not found") from exc
            else:
                user = await repo.upsert_user(
                    tenant_id=tenant_id,
                    source_id=source_id,
                    user_name=str(payload.get("userName") or source_id),
                    display_name=str(
                        payload.get("displayName") or payload.get("userName") or source_id
                    ),
                    active=True,
                )
        return _scim_user_response(user)

    @app.post("/scim/v2/{tenant_id}/Groups", status_code=status.HTTP_201_CREATED)
    async def create_scim_group(
        tenant_id: str, payload: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        pool = await scim_pool(request, tenant_id)
        group = _scim_group_request(payload)
        async with pool.connection() as conn:
            created = await AsyncSCIMRepository(conn).upsert_group(tenant_id=tenant_id, **group)
        return _scim_group_response(created)

    @app.put("/scim/v2/{tenant_id}/Groups/{source_id}")
    async def update_scim_group(
        tenant_id: str, source_id: str, payload: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        pool = await scim_pool(request, tenant_id)
        group = _scim_group_request({**payload, "id": source_id})
        async with pool.connection() as conn:
            updated = await AsyncSCIMRepository(conn).upsert_group(tenant_id=tenant_id, **group)
        return _scim_group_response(updated)


def _request_bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    return None


def _require_scim_token(request: Request, settings: Config, tenant_id: str) -> None:
    token = _request_bearer_token(request)
    if token is None:
        raise HTTPException(status_code=401, detail="SCIM bearer token required")
    expected_tenant = settings.scim_tokens.get(token)
    if expected_tenant is None:
        raise HTTPException(status_code=401, detail="Invalid SCIM bearer token")
    if expected_tenant != tenant_id:
        raise HTTPException(status_code=403, detail="SCIM token is not scoped to this tenant")


def _scim_user_request(payload: dict[str, Any]) -> dict[str, Any]:
    source_id = str(payload.get("id") or payload.get("externalId") or payload.get("userName") or "")
    user_name = str(payload.get("userName") or source_id)
    display_name = str(payload.get("displayName") or user_name)
    if not source_id or not user_name:
        raise HTTPException(status_code=400, detail="SCIM user id and userName are required")
    return {
        "source_id": source_id,
        "user_name": user_name,
        "display_name": display_name,
        "active": bool(payload.get("active", True)),
    }


def _scim_group_request(payload: dict[str, Any]) -> dict[str, Any]:
    source_id = str(
        payload.get("id") or payload.get("externalId") or payload.get("displayName") or ""
    )
    display_name = str(payload.get("displayName") or source_id)
    if not source_id or not display_name:
        raise HTTPException(status_code=400, detail="SCIM group id and displayName are required")
    members = [
        str(member.get("value"))
        for member in payload.get("members", [])
        if isinstance(member, dict) and member.get("value")
    ]
    return {
        "source_id": source_id,
        "display_name": display_name,
        "members": members,
        "active": bool(payload.get("active", True)),
    }


def _scim_active_from_patch(payload: dict[str, Any]) -> bool | None:
    if "active" in payload:
        return bool(payload["active"])
    for op in payload.get("Operations", []):
        if not isinstance(op, dict):
            continue
        if str(op.get("path", "")).lower() == "active":
            return bool(op.get("value"))
    return None


def _scim_user_response(user: SCIMUser) -> dict[str, Any]:
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "id": user.source_id,
        "userName": user.user_name,
        "displayName": user.display_name,
        "active": user.active,
        "externalId": user.source_id,
        "meta": {"resourceType": "User"},
    }


def _scim_group_response(group: SCIMGroup) -> dict[str, Any]:
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
        "id": group.source_id,
        "displayName": group.display_name,
        "active": group.active,
        "members": [{"value": member} for member in group.members],
        "meta": {"resourceType": "Group"},
    }


def _user_from_row(row: _Row, user_name: str) -> SCIMUser:
    return SCIMUser(
        id=str(row["id"]),
        source_id=str(row["managed_source_id"]),
        user_name=user_name,
        display_name=str(row["display_name"]),
        tenant_id=str(row["tenant_id"]),
        active=bool(row["active"]),
    )


def _group_from_row(row: _Row, members: list[str]) -> SCIMGroup:
    return SCIMGroup(
        id=str(row["id"]),
        source_id=str(row["source_id"]),
        display_name=str(row["display_name"]),
        tenant_id=str(row["tenant_id"]),
        active=bool(row["active"]),
        members=members,
    )


def _external_subject(tenant_id: str, source_id: str, user_name: str | None = None) -> str:
    suffix = user_name or source_id
    return f"scim:{tenant_id}:{source_id}:{suffix}"


def _user_name_from_external(external_subject: str) -> str:
    return str(external_subject).rsplit(":", 1)[-1]
