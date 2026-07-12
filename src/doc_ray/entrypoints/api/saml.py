"""Tenant-scoped SAML assertion consumer service (BL-067)."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from inspect import isawaitable
from types import TracebackType
from typing import Protocol, cast

import defusedxml.ElementTree as ET
from fastapi import FastAPI, Form, HTTPException, Request

from doc_ray.entrypoints.api.security import Principal


@dataclass(frozen=True)
class SAMLMetadata:
    """Tenant IdP metadata relevant to doc-ray login."""

    entity_id: str
    sso_url: str | None
    x509_certificates: list[str]


@dataclass(frozen=True)
class SAMLAssertion:
    """Validated SAML assertion normalized to doc-ray identity inputs."""

    subject: str
    external_subject: str
    attributes: dict[str, str]
    scopes: frozenset[str]


class _Cursor(Protocol):
    def fetchone(self) -> object:
        ...


class _Connection(Protocol):
    def execute(self, query: str, params: tuple[object, ...] = ...) -> object:
        ...


class _ConnectionContext(Protocol):
    async def __aenter__(self) -> _Connection:
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


class SAMLPrincipalPool(Protocol):
    """Pool surface needed to resolve SAML principals."""

    def connection(
        self,
    ) -> AbstractAsyncContextManager[_Connection]:
        """Return an async connection context manager."""
        ...


class PostgresSAMLPrincipalResolver:
    """Resolve SAML NameID assertions through ``principals.external_subject``."""

    def __init__(self, pool: SAMLPrincipalPool, *, auto_create: bool = False) -> None:
        """Store an async psycopg pool-like object."""
        self._pool = pool
        self._auto_create = auto_create

    async def __call__(self, *, tenant_id: str, assertion: SAMLAssertion) -> Principal | None:
        """Return the principal matching a SAML assertion, or None when unmapped."""
        async with self._pool.connection() as conn:
            cur = await _execute(
                conn,
                "SELECT id FROM principals WHERE tenant_id = %s AND external_subject = %s",
                (tenant_id, assertion.external_subject),
            )
            row = await _fetchone(cur)
            if row is None and self._auto_create:
                cur = await _execute(
                    conn,
                    "INSERT INTO principals (kind, display_name, tenant_id, external_subject) "
                    "VALUES ('human', %s, %s, %s) RETURNING id",
                    (
                        _display_name(assertion),
                        tenant_id,
                        assertion.external_subject,
                    ),
                )
                row = await _fetchone(cur)
        if row is None:
            return None
        row_mapping = cast(Mapping[str, object], row)
        principal_id = row_mapping["id"]
        return Principal(
            subject=assertion.external_subject,
            principal_id=str(principal_id),
            scopes=assertion.scopes,
        )


def parse_saml_metadata(metadata_xml: str) -> SAMLMetadata:
    """Parse IdP metadata XML into the fields needed by the ACS route."""
    root = ET.fromstring(metadata_xml)
    entity_id = str(root.attrib.get("entityID") or "").strip()
    if not entity_id:
        raise ValueError("SAML metadata missing entityID")
    sso_url: str | None = None
    certs: list[str] = []
    for elem in root.iter():
        name = _local_name(elem.tag)
        if name == "SingleSignOnService" and sso_url is None:
            location = elem.attrib.get("Location")
            if location:
                sso_url = str(location)
        elif name == "X509Certificate" and elem.text:
            certs.append("".join(elem.text.split()))
    return SAMLMetadata(entity_id=entity_id, sso_url=sso_url, x509_certificates=certs)


def validate_saml_response(
    saml_response: str,
    *,
    tenant_id: str,
    idp_entity_id: str,
    service_provider_entity_id: str,
    allow_unsigned: bool,
    now: datetime | None = None,
) -> SAMLAssertion:
    """Validate a base64 SAML response and return its normalized assertion."""
    root = ET.fromstring(base64.b64decode(saml_response))
    if _local_name(root.tag) != "Response":
        raise ValueError("SAML response root must be Response")
    has_signature = _has_signature(root)
    if not allow_unsigned:
        if not has_signature:
            raise ValueError("SAML assertion is unsigned")
        raise ValueError("SAML XMLDSIG verification is not available")
    assertion = _first_child(root, "Assertion")
    issuer = _text(_first_child(assertion, "Issuer")) or _text(_first_child(root, "Issuer"))
    if issuer != idp_entity_id:
        raise ValueError("SAML issuer mismatch")
    subject = _text(_first_descendant(assertion, "NameID"))
    if not subject:
        raise ValueError("SAML subject is required")
    _validate_conditions(
        assertion,
        service_provider_entity_id=service_provider_entity_id,
        now=now or datetime.now(UTC),
    )
    attributes = _attributes(assertion)
    scopes = _scopes(attributes)
    return SAMLAssertion(
        subject=subject,
        external_subject=f"saml:{tenant_id}:{subject}",
        attributes=attributes,
        scopes=scopes,
    )


def register_saml_routes(app: FastAPI) -> None:
    """Register tenant-scoped SAML assertion consumer routes."""

    @app.post("/auth/saml/{tenant_id}/acs")
    async def saml_acs(
        tenant_id: str,
        request: Request,
        saml_response: str = Form(alias="SAMLResponse"),
    ) -> dict[str, object]:
        pool = cast(_PoolLike, request.app.state.pool)
        async with pool.connection() as conn:
            config = await _tenant_saml_config(conn, tenant_id)
            if config is None:
                raise HTTPException(status_code=404, detail="SAML tenant configuration not found")
            try:
                assertion = validate_saml_response(
                    saml_response,
                    tenant_id=tenant_id,
                    idp_entity_id=str(config["entity_id"]),
                    service_provider_entity_id=str(config["service_provider_entity_id"]),
                    allow_unsigned=bool(config["allow_unsigned_assertions"]),
                )
                principal = await PostgresSAMLPrincipalResolver(
                    cast(SAMLPrincipalPool, _SingleConnectionPool(conn)),
                    auto_create=bool(config["jit_provisioning"]),
                )(tenant_id=tenant_id, assertion=assertion)
            except ValueError as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from exc
            if principal is None or principal.principal_id is None:
                raise HTTPException(status_code=401, detail="SAML subject is not mapped")
        return {
            "subject": principal.subject,
            "principal_id": principal.principal_id,
            "scopes": sorted(principal.scopes),
            "token_type": "SAML",
        }


async def _tenant_saml_config(conn: _Connection, tenant_id: str) -> Mapping[str, object] | None:
    cur = await _execute(
        conn,
        """
        SELECT tenant_id, entity_id, metadata_xml, metadata_url, service_provider_entity_id,
               acs_url, allow_unsigned_assertions, jit_provisioning
          FROM tenant_saml_identity_providers
         WHERE tenant_id = %s
        """,
        (tenant_id,),
    )
    row = await _fetchone(cur)
    return row if isinstance(row, Mapping) else None


async def _execute(conn: _Connection, query: str, params: tuple[object, ...]) -> _Cursor:
    result = conn.execute(query, params)
    if isawaitable(result):
        result = await result
    return cast(_Cursor, result)


async def _fetchone(cursor: _Cursor) -> object:
    result = cursor.fetchone()
    if isawaitable(result):
        return await result
    return result


def _validate_conditions(
    assertion: ET.Element,
    *,
    service_provider_entity_id: str,
    now: datetime,
) -> None:
    conditions = _first_child(assertion, "Conditions")
    current = _as_utc(now)
    not_before = conditions.attrib.get("NotBefore")
    if not_before and current < _parse_saml_time(not_before):
        raise ValueError("SAML assertion is not yet valid")
    not_on_or_after = conditions.attrib.get("NotOnOrAfter")
    if not_on_or_after and current >= _parse_saml_time(not_on_or_after):
        raise ValueError("SAML assertion expired")
    audiences = [
        _text(elem)
        for elem in conditions.iter()
        if _local_name(elem.tag) == "Audience" and _text(elem)
    ]
    if service_provider_entity_id not in audiences:
        raise ValueError("SAML audience mismatch")


def _attributes(assertion: ET.Element) -> dict[str, str]:
    out: dict[str, str] = {}
    for attr in assertion.iter():
        if _local_name(attr.tag) != "Attribute":
            continue
        name = attr.attrib.get("Name")
        if not name:
            continue
        values = [
            _text(child)
            for child in list(attr)
            if _local_name(child.tag) == "AttributeValue" and _text(child)
        ]
        if values:
            out[str(name)] = values[0]
    return out


def _scopes(attributes: Mapping[str, str]) -> frozenset[str]:
    scope = attributes.get("scope")
    if scope:
        scopes = frozenset(part for part in scope.split() if part)
        return scopes or frozenset({"read"})
    return frozenset({"read"})


def _has_signature(root: ET.Element) -> bool:
    return any(_local_name(elem.tag) == "Signature" for elem in root.iter())


def _first_child(parent: ET.Element, local_name: str) -> ET.Element:
    for elem in list(parent):
        if _local_name(elem.tag) == local_name:
            return elem
    raise ValueError(f"SAML {local_name} element is required")


def _first_descendant(parent: ET.Element, local_name: str) -> ET.Element:
    for elem in parent.iter():
        if _local_name(elem.tag) == local_name:
            return elem
    raise ValueError(f"SAML {local_name} element is required")


def _text(elem: ET.Element) -> str:
    return (elem.text or "").strip()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _parse_saml_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _display_name(assertion: SAMLAssertion) -> str:
    for key in ("displayName", "email", "mail"):
        value = assertion.attributes.get(key)
        if value:
            return value
    return f"saml:{assertion.subject}"


class _SingleConnectionPool:
    def __init__(self, conn: _Connection) -> None:
        self._conn = conn

    def connection(self) -> _SingleConnectionContext:
        return _SingleConnectionContext(self._conn)


class _SingleConnectionContext:
    def __init__(self, conn: _Connection) -> None:
        self._conn = conn

    async def __aenter__(self) -> _Connection:
        return self._conn

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None
