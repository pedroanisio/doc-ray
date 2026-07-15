"""Tests for auth administration use cases."""

from __future__ import annotations

from pathlib import Path

import _itdb
import pytest

from doc_ray.adapters.auth_admin_postgres import PostgresAuthAdminRepository
from doc_ray.application.auth_admin import (
    ApiTokenEntry,
    append_api_token_entry,
    format_env_assignment,
    generate_api_token,
    parse_scope_text,
    upsert_env_value,
)

_ROOT = Path(__file__).resolve().parents[3]


def test_parse_scope_text_sorts_and_rejects_empty_scopes():
    assert parse_scope_text("write+read") == frozenset({"read", "write"})

    with pytest.raises(ValueError, match="scope list cannot be empty"):
        parse_scope_text("+")


def test_parse_scope_text_allows_impersonate_scope():
    # The webapp's service token needs `impersonate` to act on behalf of the
    # logged-in user; the runtime parser honours it, so the CLI must mint it.
    assert parse_scope_text("read+write+impersonate") == frozenset({"read", "write", "impersonate"})


def test_parse_scope_text_still_rejects_unknown_scopes():
    with pytest.raises(ValueError, match="unknown scope"):
        parse_scope_text("read+bogus")


def test_format_env_assignment_matches_runtime_parser_format():
    entry = ApiTokenEntry(
        token="secret-token",
        principal_id="00000000-0000-0000-0000-000000000123",
        scopes=frozenset({"write", "read"}),
    )

    assert (
        format_env_assignment(entry)
        == "secret-token=00000000-0000-0000-0000-000000000123:read+write"
    )


def test_append_api_token_entry_preserves_existing_entries():
    entry = ApiTokenEntry(
        token="new-secret",
        principal_id="00000000-0000-0000-0000-000000000456",
        scopes=frozenset({"read"}),
    )

    assert (
        append_api_token_entry("old=00000000-0000-0000-0000-000000000123:read", entry)
        == "old=00000000-0000-0000-0000-000000000123:read,"
        "new-secret=00000000-0000-0000-0000-000000000456:read"
    )


def test_append_api_token_entry_rejects_duplicate_token():
    entry = ApiTokenEntry(
        token="old",
        principal_id="00000000-0000-0000-0000-000000000456",
        scopes=frozenset({"read"}),
    )

    with pytest.raises(ValueError, match="already exists"):
        append_api_token_entry("old=00000000-0000-0000-0000-000000000123:read", entry)


def test_upsert_env_value_updates_or_appends_key(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=postgresql://example\nDOCRAY_API_TOKENS=old=value\n")

    upsert_env_value(env_file, "DOCRAY_API_TOKENS", "new=value")
    upsert_env_value(env_file, "DOCRAY_API_ALLOW_ANONYMOUS", "false")

    assert env_file.read_text() == (
        "DATABASE_URL=postgresql://example\n"
        "DOCRAY_API_TOKENS=new=value\n"
        "DOCRAY_API_ALLOW_ANONYMOUS=false\n"
    )


def test_generate_api_token_uses_urlsafe_entropy():
    token = generate_api_token()

    assert len(token) >= 32
    assert "=" not in token and "," not in token and ";" not in token


def test_postgres_repository_creates_principal_role_and_document_grant(pg):
    conn, created = pg
    repo = PostgresAuthAdminRepository(conn)
    suffix = _itdb.unique_sha()[:8]
    display_name = f"auth-admin-{suffix}"
    doc_id = _itdb.make_doc(conn, f"Auth admin document {suffix}.")
    created.append(doc_id)

    principal = repo.create_principal(display_name=display_name, kind="service")
    repo.assign_role(principal.id, "curator")
    repo.grant_document(principal.id, doc_id, "annotate")

    role_row = conn.execute(
        "SELECT count(*) AS n FROM principal_roles pr "
        "JOIN roles r ON r.id = pr.role_id "
        "WHERE pr.principal_id = %s AND r.name = 'curator'",
        (principal.id,),
    ).fetchone()
    grant_row = conn.execute(
        "SELECT permission FROM document_acl WHERE principal_id = %s AND document_id = %s",
        (principal.id, doc_id),
    ).fetchone()

    assert role_row["n"] == 1
    assert grant_row["permission"] == "annotate"

    conn.execute("DELETE FROM principals WHERE id = %s", (principal.id,))


def test_tenant_foundation_migration_adds_tenant_partitioning_sql():
    sql = (_ROOT / "db/migrations/061_tenant_foundation.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS tenants" in sql
    assert "ALTER TABLE principals" in sql and "ADD COLUMN IF NOT EXISTS tenant_id" in sql
    assert "ALTER TABLE documents" in sql and "ADD COLUMN IF NOT EXISTS tenant_id" in sql
    assert "CREATE OR REPLACE FUNCTION docray_current_tenant()" in sql
    assert "d.tenant_id = docray_current_tenant()" in sql
    assert "GRANT SELECT ON tenants TO docray_client" in sql
    assert "default tenant" in sql


def test_postgres_repository_creates_tenant_scoped_principal(pg):
    conn, _created = pg
    repo = PostgresAuthAdminRepository(conn)
    suffix = _itdb.unique_sha()[:8]
    tenant_slug = f"tenant-{suffix}"
    principal_name = f"tenant-principal-{suffix}"

    conn.execute((_ROOT / "db/migrations/061_tenant_foundation.sql").read_text())
    tenant = repo.create_tenant(slug=tenant_slug, display_name=f"Tenant {suffix}")
    principal = repo.create_principal(
        display_name=principal_name,
        kind="service",
        tenant_id=tenant.id,
    )

    row = conn.execute(
        "SELECT tenant_id FROM principals WHERE id = %s",
        (principal.id,),
    ).fetchone()

    assert principal.tenant_id == tenant.id
    assert str(row["tenant_id"]) == tenant.id

    conn.execute("DELETE FROM principals WHERE id = %s", (principal.id,))
    conn.execute("DELETE FROM tenants WHERE id = %s", (tenant.id,))
