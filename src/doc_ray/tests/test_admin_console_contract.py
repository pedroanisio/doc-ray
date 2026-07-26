"""Regression guards for admin-console GraphQL contract drift."""

from __future__ import annotations

import re
from pathlib import Path

from graphql import parse, validate

from doc_ray.entrypoints.api.schema import build_schema

_ROOT = Path(__file__).resolve().parents[3]
_API_DIR = _ROOT / "admin-console" / "src" / "api"
_TEMPLATE_CONST_RE = re.compile(r"const\s+([A-Z_]+)\s*=\s*`([\s\S]*?)`;", re.MULTILINE)
_GRAPHQL_CALL_RE = re.compile(r"client\.graphql<[\s\S]*?>\(\s*`([\s\S]*?)`", re.MULTILINE)
_GRAPHQL_GENERIC_RE = re.compile(r"client\.graphql<([^>]*)>", re.MULTILINE)
_INTERFACE_RE = re.compile(r"export interface (\w+) \{([\s\S]*?)\n\}", re.MULTILINE)
_FIELD_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*)\??:", re.MULTILINE)
_GRAPHQL_TYPE_COMMENT_RE = re.compile(r"//\s*GraphQL type (\w+)")

# The historically-covered GraphQL interfaces. Discovery below must always find
# at least these; the floor turns a silently-broken discovery regex — which would
# otherwise make every guard pass vacuously — into a loud failure. See
# test_admin_console_contract_guard_is_complete.
_KNOWN_GRAPHQL_INTERFACES = frozenset(
    {"Job", "Document", "SecurityAuditEvent", "BillingEvent", "BillingStatus"}
)


def _api_files() -> list[str]:
    """Every hand-written admin-console API module (excludes ``*.test.ts``).

    Auto-discovered rather than hard-coded so a NEW api file carrying GraphQL
    operations or interfaces cannot silently escape the drift guard — the
    residual the drift-risk map flagged against the previous fixed lists.
    """
    return sorted(
        f"admin-console/src/api/{path.name}"
        for path in _API_DIR.glob("*.ts")
        if not path.name.endswith(".test.ts")
    )


def _files_with_operations() -> list[str]:
    return [
        rel
        for rel in _api_files()
        if "client.graphql<" in (_ROOT / rel).read_text(encoding="utf-8")
    ]


def _graphql_interface_names(interfaces: dict[str, str]) -> set[str]:
    """TS interfaces that mirror a GraphQL type.

    Two independent signals are unioned: an explicit ``// GraphQL type X``
    annotation (used in ``types.ts``) and any interface name referenced inside a
    ``client.graphql<...>`` result generic (how ``billing.ts`` marks its types).
    """
    annotated: set[str] = set()
    referenced_blobs: list[str] = []
    for rel in _api_files():
        source = (_ROOT / rel).read_text(encoding="utf-8")
        annotated.update(_GRAPHQL_TYPE_COMMENT_RE.findall(source))
        referenced_blobs.extend(_GRAPHQL_GENERIC_RE.findall(source))
    referenced = {
        name
        for name in interfaces
        if any(re.search(rf"\b{re.escape(name)}\b", blob) for blob in referenced_blobs)
    }
    return (annotated | referenced) & set(interfaces)


def _admin_console_graphql_documents() -> dict[str, str]:
    documents: dict[str, str] = {}
    for rel_path in _files_with_operations():
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
    for rel_path in _api_files():
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

    graphql_interfaces = _graphql_interface_names(interfaces)
    assert graphql_interfaces, "no admin-console GraphQL interfaces were discovered"

    for interface_name in sorted(graphql_interfaces):
        graphql_type = schema.get_type(interface_name)
        assert graphql_type is not None, f"missing GraphQL type {interface_name}"

        ts_fields = set(_FIELD_RE.findall(interfaces[interface_name]))
        graphql_fields = set(graphql_type.fields)

        assert ts_fields, f"{interface_name} has no parsed fields"
        drift = ts_fields - graphql_fields
        assert not drift, f"{interface_name} TS fields absent from live schema: {sorted(drift)}"


def test_admin_console_contract_guard_is_complete():
    """Coverage must not silently regress (drift-map C1 residual).

    The previous guard hard-coded its file and interface lists, so a new api
    module carrying GraphQL operations or a new GraphQL interface would escape
    validation with no failure. Auto-discovery must (a) find api files, (b) miss
    no file that calls ``client.graphql<>``, and (c) still surface every
    historically-known GraphQL interface.
    """
    files = _api_files()
    assert files, "no admin-console api files discovered — glob or path is wrong"

    documents = _admin_console_graphql_documents()
    covered_files = {name.split("#", 1)[0] for name in documents}
    assert covered_files == set(_files_with_operations()), (
        "an api file calls client.graphql<> but produced no validated document"
    )

    discovered = _graphql_interface_names(_admin_console_interfaces())
    lost = _KNOWN_GRAPHQL_INTERFACES - discovered
    assert not lost, f"GraphQL-interface discovery regressed; no longer covers: {sorted(lost)}"


def test_interface_field_guard_would_catch_an_invented_field():
    """The field-diff logic must fail on drift, not pass vacuously.

    Feeds a synthetic interface with a field the schema lacks through the same
    regexes and set difference the real guard uses, and asserts it is flagged.
    """
    schema = build_schema()._schema
    job = schema.get_type("Job")
    assert job is not None

    synthetic = "export interface Job {\n  id: string;\n  ghostField: string;\n}"
    _name, body = _INTERFACE_RE.findall(synthetic)[0]
    drift = set(_FIELD_RE.findall(body)) - set(job.fields)
    assert drift == {"ghostField"}, "the guard would NOT catch an invented interface field"


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
