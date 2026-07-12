"""Tenant-scoped SAML SSO tests (BL-067)."""

from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import _itdb
import httpx
import pytest

from doc_ray.config import Config
from doc_ray.entrypoints.api.app import create_app
from doc_ray.entrypoints.api.saml import (
    PostgresSAMLPrincipalResolver,
    SAMLAssertion,
    parse_saml_metadata,
    validate_saml_response,
)

_ROOT = Path(__file__).resolve().parents[3]
_SAML_MIGRATION = _ROOT / "db" / "migrations" / "067_saml_identity_providers.sql"


def _apply_saml_migration(conn) -> None:
    conn.execute(_SAML_MIGRATION.read_text(encoding="utf-8"))


def _metadata_xml() -> str:
    return """\
<EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata" entityID="https://idp.example/saml">
  <IDPSSODescriptor>
    <SingleSignOnService
      Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
      Location="https://idp.example/sso"/>
    <KeyDescriptor use="signing"><KeyInfo xmlns="http://www.w3.org/2000/09/xmldsig#">
      <X509Data><X509Certificate>test-cert</X509Certificate></X509Data>
    </KeyInfo></KeyDescriptor>
  </IDPSSODescriptor>
</EntityDescriptor>
"""


def _response_xml(
    *,
    tenant_id: str,
    subject: str = "user-123",
    issuer: str = "https://idp.example/saml",
    audience: str | None = None,
    now: datetime,
    email: str = "user@example.com",
) -> str:
    audience = audience or f"doc-ray:{tenant_id}"
    not_before = (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    not_after = (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    return f"""\
<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
                xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">
  <saml:Issuer>{issuer}</saml:Issuer>
  <saml:Assertion>
    <saml:Issuer>{issuer}</saml:Issuer>
    <saml:Subject><saml:NameID>{subject}</saml:NameID></saml:Subject>
    <saml:Conditions NotBefore="{not_before}" NotOnOrAfter="{not_after}">
      <saml:AudienceRestriction><saml:Audience>{audience}</saml:Audience></saml:AudienceRestriction>
    </saml:Conditions>
    <saml:AttributeStatement>
      <saml:Attribute Name="email"><saml:AttributeValue>{email}</saml:AttributeValue></saml:Attribute>
      <saml:Attribute Name="scope"><saml:AttributeValue>read write</saml:AttributeValue></saml:Attribute>
    </saml:AttributeStatement>
  </saml:Assertion>
</samlp:Response>
"""


def _b64(xml: str) -> str:
    return base64.b64encode(xml.encode()).decode()


def test_saml_migration_adds_tenant_idp_configuration() -> None:
    sql = _SAML_MIGRATION.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS tenant_saml_identity_providers" in sql
    assert "metadata_xml text" in sql
    assert "allow_unsigned_assertions boolean NOT NULL DEFAULT false" in sql
    assert "service_provider_entity_id" in sql


def test_parse_saml_metadata_extracts_entity_sso_and_certificate() -> None:
    parsed = parse_saml_metadata(_metadata_xml())

    assert parsed.entity_id == "https://idp.example/saml"
    assert parsed.sso_url == "https://idp.example/sso"
    assert parsed.x509_certificates == ["test-cert"]


def test_validate_saml_response_maps_subject_attributes_and_scopes() -> None:
    now = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
    tenant_id = str(uuid4())

    assertion = validate_saml_response(
        _b64(_response_xml(tenant_id=tenant_id, now=now)),
        tenant_id=tenant_id,
        idp_entity_id="https://idp.example/saml",
        service_provider_entity_id=f"doc-ray:{tenant_id}",
        allow_unsigned=True,
        now=now,
    )

    assert assertion == SAMLAssertion(
        subject="user-123",
        external_subject=f"saml:{tenant_id}:user-123",
        attributes={"email": "user@example.com", "scope": "read write"},
        scopes=frozenset({"read", "write"}),
    )


def test_validate_saml_response_rejects_unsigned_by_default() -> None:
    now = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
    tenant_id = str(uuid4())

    with pytest.raises(ValueError, match="unsigned"):
        validate_saml_response(
            _b64(_response_xml(tenant_id=tenant_id, now=now)),
            tenant_id=tenant_id,
            idp_entity_id="https://idp.example/saml",
            service_provider_entity_id=f"doc-ray:{tenant_id}",
            allow_unsigned=False,
            now=now,
        )


def test_validate_saml_response_rejects_wrong_tenant_audience() -> None:
    now = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
    tenant_id = str(uuid4())

    with pytest.raises(ValueError, match="audience"):
        validate_saml_response(
            _b64(_response_xml(tenant_id=tenant_id, audience="doc-ray:other", now=now)),
            tenant_id=tenant_id,
            idp_entity_id="https://idp.example/saml",
            service_provider_entity_id=f"doc-ray:{tenant_id}",
            allow_unsigned=True,
            now=now,
        )


def test_postgres_saml_resolver_maps_assertion_to_tenant_principal(pg) -> None:
    conn, _created = pg
    _apply_saml_migration(conn)
    tenant_id = conn.execute(
        "INSERT INTO tenants (slug, display_name) VALUES (%s, %s) RETURNING id",
        (f"saml-resolver-{_itdb.unique_sha()[:8]}", "SAML Resolver Tenant"),
    ).fetchone()["id"]
    principal_id = conn.execute(
        "INSERT INTO principals (kind, display_name, tenant_id, external_subject) "
        "VALUES ('human', %s, %s, %s) RETURNING id",
        (
            f"saml-user-{_itdb.unique_sha()[:8]}",
            tenant_id,
            f"saml:{tenant_id}:user-123",
        ),
    ).fetchone()["id"]
    resolver = PostgresSAMLPrincipalResolver(_Pool(conn))

    async def check() -> None:
        principal = await resolver(
            tenant_id=str(tenant_id),
            assertion=SAMLAssertion(
                subject="user-123",
                external_subject=f"saml:{tenant_id}:user-123",
                attributes={"email": "user@example.com"},
                scopes=frozenset({"read"}),
            ),
        )
        assert principal is not None
        assert principal.subject == f"saml:{tenant_id}:user-123"
        assert principal.principal_id == str(principal_id)
        assert principal.scopes == frozenset({"read"})

    asyncio.run(check())


def test_saml_acs_route_logs_in_tenant_user(pg) -> None:
    conn, _created = pg
    _apply_saml_migration(conn)
    suffix = _itdb.unique_sha()[:8]
    tenant_id = conn.execute(
        "INSERT INTO tenants (slug, display_name) VALUES (%s, %s) RETURNING id",
        (f"saml-route-{suffix}", "SAML Route Tenant"),
    ).fetchone()["id"]
    conn.execute(
        """
        INSERT INTO tenant_saml_identity_providers
            (tenant_id, entity_id, metadata_xml, service_provider_entity_id,
             allow_unsigned_assertions)
        VALUES (%s, %s, %s, %s, true)
        """,
        (tenant_id, "https://idp.example/saml", _metadata_xml(), f"doc-ray:{tenant_id}"),
    )
    principal_id = conn.execute(
        "INSERT INTO principals (kind, display_name, tenant_id, external_subject) "
        "VALUES ('human', %s, %s, %s) RETURNING id",
        (f"saml-route-user-{suffix}", tenant_id, f"saml:{tenant_id}:user-123"),
    ).fetchone()["id"]
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
    app.state.pool = _Pool(conn)

    async def exercise() -> None:
        now = datetime.now(UTC)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                f"/auth/saml/{tenant_id}/acs",
                data={
                    "SAMLResponse": _b64(
                        _response_xml(
                            tenant_id=str(tenant_id),
                            now=now,
                        )
                    )
                },
            )
        assert response.status_code == 200
        assert response.json()["principal_id"] == str(principal_id)
        assert response.json()["subject"] == f"saml:{tenant_id}:user-123"

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
