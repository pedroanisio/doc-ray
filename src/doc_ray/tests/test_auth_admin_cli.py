"""CLI tests for auth administration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from doc_ray.adapters.auth_admin_postgres import (
    PostgresAuthAdminRepository,
    PrincipalRecord,
    TenantRecord,
)
from doc_ray.entrypoints import auth_admin


@dataclass
class FakeRepository:
    """In-memory repository double for the auth admin CLI."""

    principal: PrincipalRecord = PrincipalRecord(
        id="00000000-0000-0000-0000-000000000001",
        display_name="alice",
        kind="human",
        external_subject=None,
        tenant_id="00000000-0000-0000-0000-000000000010",
    )

    def __post_init__(self) -> None:
        self.tenant: TenantRecord = TenantRecord(
            id="00000000-0000-0000-0000-000000000010",
            slug="default",
            display_name="Default tenant",
        )
        self.tenants: list[tuple[str, str]] = []
        self.created: list[tuple[str, str, str | None, str | None]] = []
        self.roles: list[tuple[str, str]] = []
        self.grants: list[tuple[str, str, str]] = []
        self.logins: list[tuple[str, str, str | None]] = []

    def create_tenant(self, *, slug: str, display_name: str) -> TenantRecord:
        self.tenants.append((slug, display_name))
        self.tenant = TenantRecord(
            id="00000000-0000-0000-0000-000000000011",
            slug=slug,
            display_name=display_name,
        )
        return self.tenant

    def resolve_tenant(self, value: str) -> TenantRecord:
        assert value in {self.tenant.id, self.tenant.slug}
        return self.tenant

    def resolve_principal(self, value: str) -> PrincipalRecord:
        assert value in {self.principal.id, self.principal.display_name}
        return self.principal

    def create_principal(
        self,
        *,
        display_name: str,
        kind: str,
        external_subject: str | None = None,
        tenant_id: str | None = None,
    ) -> PrincipalRecord:
        self.created.append((display_name, kind, external_subject, tenant_id))
        self.principal = PrincipalRecord(
            id="00000000-0000-0000-0000-000000000002",
            display_name=display_name,
            kind=kind,
            external_subject=external_subject,
            tenant_id=tenant_id or self.tenant.id,
        )
        return self.principal

    def assign_role(self, principal_id: str, role_name: str) -> None:
        self.roles.append((principal_id, role_name))

    def grant_document(self, principal_id: str, document_id: str, permission: str) -> None:
        self.grants.append((principal_id, document_id, permission))

    def create_db_login(
        self, principal_id: str, role_name: str, password: str | None = None
    ) -> str:
        self.logins.append((principal_id, role_name, password))
        return role_name

    def close(self) -> None:
        self.closed = True


def test_principal_create_assigns_requested_roles(capsys):
    repo = FakeRepository()

    rc = auth_admin.main(
        ["principal", "create", "--display-name", "bob", "--kind", "service", "--role", "admin"],
        repo_factory=lambda _database_url: repo,
    )

    assert rc == 0
    assert repo.created == [("bob", "service", None, None)]
    assert repo.roles == [("00000000-0000-0000-0000-000000000002", "admin")]
    assert "principal_id=00000000-0000-0000-0000-000000000002" in capsys.readouterr().out


def test_tenant_create_prints_tenant_id_and_slug(capsys):
    repo = FakeRepository()

    rc = auth_admin.main(
        ["tenant", "create", "--slug", "acme", "--display-name", "Acme Corp"],
        repo_factory=lambda _database_url: repo,
    )

    assert rc == 0
    assert repo.tenants == [("acme", "Acme Corp")]
    out = capsys.readouterr().out
    assert "tenant_id=00000000-0000-0000-0000-000000000011" in out
    assert "slug=acme" in out


def test_principal_create_accepts_tenant_slug(capsys):
    repo = FakeRepository()
    repo.create_tenant(slug="acme", display_name="Acme Corp")

    rc = auth_admin.main(
        ["principal", "create", "--display-name", "bob", "--tenant", "acme"],
        repo_factory=lambda _database_url: repo,
    )

    assert rc == 0
    assert repo.created == [("bob", "human", None, "00000000-0000-0000-0000-000000000011")]
    assert "tenant_id=00000000-0000-0000-0000-000000000011" in capsys.readouterr().out


def test_token_mint_appends_env_entry_without_printing_env_file_contents(tmp_path: Path, capsys):
    repo = FakeRepository()
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=postgresql://example\nDOCRAY_API_TOKENS=old=uuid:read\n")

    rc = auth_admin.main(
        [
            "token",
            "mint",
            "--principal",
            "alice",
            "--token",
            "new-secret",
            "--scopes",
            "read+write",
            "--env-file",
            str(env_file),
        ],
        repo_factory=lambda _database_url: repo,
    )

    assert rc == 0
    assert env_file.read_text() == (
        "DATABASE_URL=postgresql://example\n"
        "DOCRAY_API_TOKENS=old=uuid:read,"
        "new-secret=00000000-0000-0000-0000-000000000001:read+write\n"
    )
    out = capsys.readouterr().out
    assert "token=new-secret" in out
    assert "old=uuid" not in out


def test_mcp_token_mint_defaults_to_read_write_scope(capsys):
    repo = FakeRepository()

    rc = auth_admin.main(
        ["mcp-token", "mint", "--principal", "alice", "--token", "mcp-secret"],
        repo_factory=lambda _database_url: repo,
    )

    assert rc == 0
    assert "DOCRAY_API_TOKENS=mcp-secret=00000000-0000-0000-0000-000000000001:read+write" in (
        capsys.readouterr().out
    )


def test_db_login_create_reads_password_from_stdin(monkeypatch, capsys):
    repo = FakeRepository()
    monkeypatch.setattr("sys.stdin", type("Input", (), {"read": lambda self: "pw-from-stdin\n"})())

    rc = auth_admin.main(
        [
            "db-login",
            "create",
            "--principal",
            "alice",
            "--role-name",
            "docray_alice",
            "--password-stdin",
        ],
        repo_factory=lambda _database_url: repo,
    )

    assert rc == 0
    assert repo.logins == [
        ("00000000-0000-0000-0000-000000000001", "docray_alice", "pw-from-stdin")
    ]
    assert "db_role=docray_alice" in capsys.readouterr().out


def test_grant_document_records_acl_grant(capsys):
    repo = FakeRepository()

    rc = auth_admin.main(
        [
            "grant",
            "document",
            "--principal",
            "alice",
            "--document-id",
            "00000000-0000-0000-0000-000000000099",
            "--permission",
            "annotate",
        ],
        repo_factory=lambda _database_url: repo,
    )

    assert rc == 0
    assert repo.grants == [
        (
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000099",
            "annotate",
        )
    ]
    assert "permission=annotate" in capsys.readouterr().out


def test_invalid_token_scope_reports_error_and_closes_repository(capsys):
    repo = FakeRepository()
    repo.closed = False

    rc = auth_admin.main(
        ["token", "mint", "--principal", "alice", "--scopes", "admin"],
        repo_factory=lambda _database_url: repo,
    )

    assert rc == 1
    assert repo.closed is True
    assert "unknown scope(s): admin" in capsys.readouterr().err


class _Rows:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeAuthConn:
    def __init__(self) -> None:
        self.closed = False
        self.executed: list[tuple[str, tuple]] = []

    def close(self) -> None:
        self.closed = True

    def execute(self, query: str, params=()):
        text = " ".join(query.split())
        self.executed.append((text, tuple(params)))
        if "FROM principals WHERE id = %s" in text:
            return _Rows(
                {
                    "id": params[0],
                    "display_name": "alice",
                    "kind": "human",
                    "external_subject": None,
                    "tenant_id": "00000000-0000-0000-0000-000000000010",
                }
            )
        if "FROM principals WHERE display_name = %s" in text:
            return _Rows(None)
        if "RETURNING id, display_name, kind, external_subject, tenant_id" in text:
            return _Rows(
                {
                    "id": "00000000-0000-0000-0000-000000000002",
                    "display_name": params[1],
                    "kind": params[0],
                    "external_subject": params[2],
                    "tenant_id": params[3] or "00000000-0000-0000-0000-000000000010",
                }
            )
        if "RETURNING id, slug, display_name" in text:
            return _Rows(
                {
                    "id": "00000000-0000-0000-0000-000000000011",
                    "slug": params[0],
                    "display_name": params[1],
                }
            )
        if "FROM tenants WHERE slug = %s" in text:
            return _Rows(
                {
                    "id": "00000000-0000-0000-0000-000000000011",
                    "slug": params[0],
                    "display_name": "Acme",
                }
            )
        if "SELECT id FROM roles WHERE name = %s" in text:
            return _Rows({"id": "role-1"} if params[0] == "admin" else None)
        if "docray_create_principal_login" in text:
            return _Rows({"role_name": params[1]})
        return _Rows(None)


def test_postgres_auth_admin_repository_maps_common_operations():
    conn = _FakeAuthConn()
    repo = PostgresAuthAdminRepository(conn)

    principal = repo.resolve_principal("00000000-0000-0000-0000-000000000001")
    created = repo.create_principal(display_name="bot", kind="service")
    tenant = repo.create_tenant(slug="acme", display_name="Acme")
    resolved_tenant = repo.resolve_tenant("acme")
    repo.assign_role(created.id, "admin")
    repo.grant_document(created.id, "00000000-0000-0000-0000-000000000099", "read")
    role_name = repo.create_db_login(created.id, "docray_bot", "pw")
    repo.close()

    assert principal.display_name == "alice"
    assert created.kind == "service"
    assert tenant.slug == "acme"
    assert resolved_tenant.id == tenant.id
    assert role_name == "docray_bot"
    assert conn.closed is True
    assert any("INSERT INTO document_acl" in query for query, _params in conn.executed)


def test_postgres_auth_admin_repository_reports_unknown_rows():
    conn = _FakeAuthConn()
    repo = PostgresAuthAdminRepository(conn)

    try:
        repo.resolve_principal("missing")
    except ValueError as exc:
        assert "unknown principal: missing" in str(exc)
    else:
        raise AssertionError("expected unknown principal error")

    try:
        repo.assign_role("00000000-0000-0000-0000-000000000001", "missing")
    except ValueError as exc:
        assert "unknown role: missing" in str(exc)
    else:
        raise AssertionError("expected unknown role error")
