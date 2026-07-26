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


# --- operator-health.v1 REST contract (issue #22) ---------------------------
# The admin-console OperatorHealth types are NOT GraphQL; they mirror the REST
# contract in webapp/health_contract.py (operator-health.v1). Nothing guarded that
# before, which let the console read flat `corpus`/`jobs`/`issues.message` while the
# contract nests them (corpus.totals, corpus.jobs, corpus/database.issues.detail)
# — the panel rendered empty even once routing was fixed. These guards tie the two.

_HEALTH_CONTRACT = "webapp/health_contract.py"
_TS_TYPES = "admin-console/src/api/types.ts"
_NGINX_TEMPLATE = "admin-console/nginx/default.conf.template"
_TOTAL_KEYS_RE = re.compile(r"TOTAL_KEYS\s*=\s*\(([\s\S]*?)\)", re.MULTILINE)


def _ts_interfaces(rel_path: str) -> dict[str, str]:
    source = (_ROOT / rel_path).read_text(encoding="utf-8")
    return dict(_INTERFACE_RE.findall(source))


def test_operator_health_ts_type_matches_the_v1_contract():
    """admin-console OperatorHealth must mirror webapp/health_contract.py (#22)."""
    contract = (_ROOT / _HEALTH_CONTRACT).read_text(encoding="utf-8")
    total_keys = set(re.findall(r'"([a-z_]+)"', _TOTAL_KEYS_RE.search(contract).group(1)))
    assert total_keys, "could not parse TOTAL_KEYS from health_contract.py"

    interfaces = _ts_interfaces(_TS_TYPES)

    # Top-level shape: nested corpus/database, not flat metrics.
    top = set(_FIELD_RE.findall(interfaces["OperatorHealth"]))
    assert {"schema_version", "corpus", "database"} <= top, top

    corpus = set(_FIELD_RE.findall(interfaces["OperatorHealthCorpus"]))
    assert {"totals", "issues", "jobs"} <= corpus, corpus

    # Corpus totals cover every metric the contract emits.
    totals = set(_FIELD_RE.findall(interfaces["OperatorHealthTotals"]))
    missing = total_keys - totals
    assert not missing, f"OperatorHealthTotals is missing v1 totals: {sorted(missing)}"

    # Issues carry `detail` (the v1 field), never the drifted `message`.
    issue = set(_FIELD_RE.findall(interfaces["OperatorIssue"]))
    assert {"code", "severity", "detail"} <= issue, issue
    assert "message" not in issue, (
        "OperatorIssue.message is the pre-#22 drift; the contract uses `detail`"
    )


def test_nginx_template_proxies_operator_to_the_webapp():
    """The console front door must route /operator/* to the webapp, not the SPA (#22, defect 1).

    A missing `location /operator/` block is exactly what makes the SPA fallback
    serve index.html (HTTP 200 + HTML) for /operator/health.json — the incident.
    """
    template = (_ROOT / _NGINX_TEMPLATE).read_text(encoding="utf-8")
    assert "location /operator/" in template, "nginx must proxy /operator/* to the webapp"

    operator_block = template.split("location /operator/", 1)[1].split("location", 1)[0]
    assert "${WEBAPP_UPSTREAM}" in operator_block, "/operator/ must proxy to WEBAPP_UPSTREAM"
    assert "proxy_pass" in operator_block, "/operator/ must proxy_pass, not fall through to the SPA"
    # The SPA fallback still exists — but as a SEPARATE location, so /operator/ wins.
    assert "try_files" in template and "/index.html" in template
