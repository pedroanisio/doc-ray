"""Regression guards for admin-console GraphQL contract drift."""

from __future__ import annotations

import re
from pathlib import Path

from graphql import parse, validate

from doc_ray.entrypoints.api.schema import build_schema

_ROOT = Path(__file__).resolve().parents[3]
_ADMIN_API_FILES = (
    "admin-console/src/api/billing.ts",
    "admin-console/src/api/audit.ts",
    "admin-console/src/api/backups.ts",
    "admin-console/src/api/documents.ts",
    "admin-console/src/api/jobs.ts",
)
_ADMIN_INTERFACE_FILES = (
    "admin-console/src/api/types.ts",
    "admin-console/src/api/billing.ts",
)
_TEMPLATE_CONST_RE = re.compile(r"const\s+([A-Z_]+)\s*=\s*`([\s\S]*?)`;", re.MULTILINE)
_GRAPHQL_CALL_RE = re.compile(r"client\.graphql<[\s\S]*?>\(\s*`([\s\S]*?)`", re.MULTILINE)
_INTERFACE_RE = re.compile(r"export interface (\w+) \{([\s\S]*?)\n\}", re.MULTILINE)
_FIELD_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*)\??:", re.MULTILINE)
_GRAPHQL_INTERFACES = {
    "BillingEvent": "BillingEvent",
    "BillingStatus": "BillingStatus",
    "Document": "Document",
    "Job": "Job",
    "SecurityAuditEvent": "SecurityAuditEvent",
}


def _admin_console_graphql_documents() -> dict[str, str]:
    documents: dict[str, str] = {}
    for rel_path in _ADMIN_API_FILES:
        source = (_ROOT / rel_path).read_text(encoding="utf-8")
        constants = dict(_TEMPLATE_CONST_RE.findall(source))
        for index, document in enumerate(_GRAPHQL_CALL_RE.findall(source), start=1):
            for name, replacement in constants.items():
                document = document.replace(f"${{{name}}}", replacement)
            unresolved = re.findall(r"\$\{([^}]+)\}", document)
            assert not unresolved, f"{rel_path} has unresolved GraphQL template refs: {unresolved}"
            documents[f"{rel_path}#{index}"] = document
    return documents


def _admin_console_interfaces() -> dict[str, str]:
    interfaces: dict[str, str] = {}
    for rel_path in _ADMIN_INTERFACE_FILES:
        source = (_ROOT / rel_path).read_text(encoding="utf-8")
        interfaces.update(dict(_INTERFACE_RE.findall(source)))
    return interfaces


def test_admin_console_graphql_documents_validate_against_live_schema():
    """Admin-console operation strings must stay valid against Strawberry SDL."""
    schema = build_schema()._schema
    documents = _admin_console_graphql_documents()

    assert documents, "admin-console GraphQL contract guard found no operations"
    failures: dict[str, list[str]] = {}
    for name, document in documents.items():
        errors = validate(schema, parse(document))
        if errors:
            failures[name] = [str(error) for error in errors]

    assert not failures


def test_admin_console_graphql_interfaces_reference_live_fields():
    """Hand-written GraphQL TypeScript interfaces must not invent fields."""
    interfaces = _admin_console_interfaces()
    schema = build_schema()._schema

    for interface_name, graphql_type_name in _GRAPHQL_INTERFACES.items():
        assert interface_name in interfaces, f"missing TS interface {interface_name}"
        graphql_type = schema.get_type(graphql_type_name)
        assert graphql_type is not None, f"missing GraphQL type {graphql_type_name}"

        ts_fields = set(_FIELD_RE.findall(interfaces[interface_name]))
        graphql_fields = set(graphql_type.fields)

        assert ts_fields, f"{interface_name} has no parsed fields"
        assert not ts_fields - graphql_fields
