"""Collectors that enumerate doc-ray's capability surfaces from live wiring.

Each collector returns a :class:`~_feature_inventory_model.CollectorResult`. The
preferred technique is *runtime introspection*: build the real GraphQL schema,
import the real MCP tool tuples, walk the real Flask/FastAPI route tables. What
those registries contain is what the deployed system serves — no parser
approximating a decorator, no hand-maintained list to drift.

Static parsing is used only where no runtime registry exists (the environment
registry YAML, the operator script directory), and those records are marked with
the weaker ``static-declaration`` signal so the difference is visible in the
output rather than hidden in it.

A collector that raises is reported as failed, with the exception text, and the
run continues. Partial coverage is stated explicitly; it is never presented as a
complete inventory.
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path
from typing import Any

from _feature_inventory_model import (
    ROOT,
    CollectorResult,
    Feature,
    first_line,
    grep_line,
    locate,
    signature_of,
)

# The package lives under src/; webapp and scripts are deliberately outside it.
for _path in (str(ROOT / "src"), str(ROOT), str(ROOT / "scripts")):
    if _path not in sys.path:
        sys.path.insert(0, _path)


def _relative(path: str | Path) -> str:
    """Repo-relative form of ``path`` when it lives inside the tree."""
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def collect_graphql() -> CollectorResult:
    """GraphQL fields, read off the schema Strawberry actually builds."""
    result = CollectorResult("graphql", "runtime:strawberry-schema-build")
    schema = importlib.import_module("doc_ray.entrypoints.api.schema").build_schema()
    roots = (
        (schema.query, "graphql.query", "GraphQL API"),
        (schema.mutation, "graphql.mutation", "GraphQL API"),
        (schema.subscription, "graphql.subscription", "GraphQL API"),
    )
    for root, category, surface in roots:
        if root is None:
            continue
        for sfield in root.__strawberry_definition__.fields:
            resolver = getattr(sfield.base_resolver, "wrapped_func", None)
            try:
                gql_name = schema.config.name_converter.from_field(sfield)
            except Exception:  # noqa: BLE001 - name conversion is best-effort metadata
                gql_name = sfield.name
            source_file, source_line = locate(resolver) if resolver else ("", 0)
            args = ", ".join(a.python_name for a in sfield.arguments)
            result.features.append(
                Feature(
                    id=f"{category}.{gql_name}",
                    name=gql_name,
                    category=category,
                    surface=surface,
                    description=sfield.description or first_line(getattr(resolver, "__doc__", "")),
                    source_file=source_file,
                    source_line=source_line,
                    signature=f"{gql_name}({args})",
                    signals=["runtime-registry"],
                    # `name(` matches the field as it appears inside a query document,
                    # which single words like "documents" would over-match on their own.
                    probes=[f"{gql_name}(", f"{sfield.python_name}("],
                )
            )
    return result


def collect_mcp() -> CollectorResult:
    """MCP tools and ``docray://`` resources, from the tuples the server registers."""
    result = CollectorResult("mcp", "runtime:tool-registry")
    mcp = {
        name: importlib.import_module(f"doc_ray.entrypoints.mcp.{name}")
        for name in ("corpus", "structure", "compose", "mutations", "resources")
    }
    groups = (
        ("corpus", "mcp.tool.read", "MCP server (read)", ""),
        ("structure", "mcp.tool.read", "MCP server (read)", ""),
        ("compose", "mcp.tool.read", "MCP server (read)", ""),
        (
            "mutations",
            "mcp.tool.write",
            "MCP server (write)",
            "Registered only when DOCRAY_MCP_ENABLE_WRITE is set.",
        ),
    )
    for module_name, category, surface, note in groups:
        for fn in getattr(mcp[module_name], "TOOLS", ()):
            source_file, source_line = locate(fn)
            result.features.append(
                Feature(
                    id=f"{category}.{fn.__name__}",
                    name=fn.__name__,
                    category=category,
                    surface=surface,
                    description=first_line(fn.__doc__),
                    source_file=source_file,
                    source_line=source_line,
                    signature=signature_of(fn),
                    signals=["runtime-registry"],
                    probes=[fn.__name__],
                    notes=note,
                )
            )
    for spec in getattr(mcp["resources"], "RESOURCES", ()):
        source_file, source_line = locate(spec.fn)
        result.features.append(
            Feature(
                id=f"mcp.resource.{spec.uri}",
                name=spec.uri,
                category="mcp.resource",
                surface="MCP server (resources)",
                description=first_line(spec.description),
                source_file=source_file,
                source_line=source_line,
                signature=spec.mime_type,
                signals=["runtime-registry"],
                probes=[spec.uri, spec.fn.__name__],
            )
        )
    return result


def _walk_routes(routes: Any) -> Any:
    """Yield leaf routes, descending into included/mounted sub-routers."""
    for route in routes:
        nested = getattr(route, "routes", None)
        if nested:
            yield from _walk_routes(nested)
        else:
            yield route


def collect_http_routes() -> CollectorResult:
    """HTTP endpoints on the FastAPI app, from its live route table."""
    result = CollectorResult("http", "runtime:fastapi-route-table")
    app = importlib.import_module("doc_ray.entrypoints.api.app").create_app()
    for route in _walk_routes(app.routes):
        path = getattr(route, "path", "") or ""
        if not path:
            continue
        methods = sorted(set(getattr(route, "methods", set()) or set()) - {"HEAD", "OPTIONS"})
        endpoint = getattr(route, "endpoint", None)
        source_file, source_line = locate(endpoint) if endpoint else ("", 0)
        verbs = ",".join(methods) or "ANY"
        result.features.append(
            Feature(
                id=f"http.route.{verbs}.{path}",
                name=f"{verbs} {path}",
                category="http.route",
                surface="HTTP API",
                description=first_line(getattr(endpoint, "__doc__", "")),
                source_file=source_file,
                source_line=source_line,
                signature=getattr(route, "name", ""),
                signals=["runtime-registry"],
                probes=[path, getattr(route, "name", "")],
            )
        )
    return result


def collect_webapp_routes() -> CollectorResult:
    """Webapp endpoints, from the Flask URL map the app builds at import time."""
    result = CollectorResult("webapp", "runtime:flask-url-map")
    app = importlib.import_module("webapp.app").app
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: str(r.rule)):
        methods = sorted(set(rule.methods or set()) - {"HEAD", "OPTIONS"})
        view = app.view_functions.get(rule.endpoint)
        source_file, source_line = locate(view) if view else ("", 0)
        verbs = ",".join(methods) or "ANY"
        result.features.append(
            Feature(
                # One view can serve several rules (e.g. the OAuth metadata aliases),
                # so the rule — not the endpoint — is what makes the id unique.
                id=f"webapp.route.{verbs}.{rule.rule}",
                name=f"{verbs} {rule.rule}",
                category="webapp.route",
                surface="Webapp",
                description=first_line(getattr(view, "__doc__", "")),
                source_file=source_file,
                source_line=source_line,
                signature=rule.endpoint,
                signals=["runtime-registry"],
                probes=[rule.endpoint, rule.rule],
            )
        )
    return result


def collect_jobs() -> CollectorResult:
    """Background job kinds, cross-checked against the worker's handler table."""
    result = CollectorResult("jobs", "runtime:job-kind-constants+handler-table")
    domain = importlib.import_module("doc_ray.domain.jobs")
    handlers = importlib.import_module("doc_ray.application.job_handlers").HANDLERS
    source_path = Path(locate(domain)[0] or "src/doc_ray/domain/jobs.py")
    for attr, kind in sorted(vars(domain).items()):
        if not attr.startswith("KIND_") or not isinstance(kind, str):
            continue
        handler = handlers.get(kind)
        signals = ["runtime-registry"]
        if handler is not None:
            signals.append("handler-bound")
        handler_file, handler_line = locate(handler) if handler else ("", 0)
        result.features.append(
            Feature(
                id=f"job.kind.{kind}",
                name=kind,
                category="job.kind",
                surface="Worker / job queue",
                description=first_line(getattr(handler, "__doc__", "")),
                source_file=handler_file or str(source_path),
                source_line=handler_line or grep_line(ROOT / source_path, rf"^{attr}\s*="),
                signature=attr,
                signals=signals,
                # The bare kind ("delete") is a common word; match the constant or the
                # quoted literal a test would actually enqueue with.
                probes=[attr, f'"{kind}"'],
                notes="" if handler else "Declared but not bound to a worker handler.",
            )
        )
    return result


def collect_ingestion_formats() -> CollectorResult:
    """Ingestible document formats, from the extractor dispatch table."""
    result = CollectorResult("formats", "runtime:extractor-dispatch-table")
    extraction = importlib.import_module("doc_ray.adapters.extraction")
    extractors = extraction._EXTRACTORS  # noqa: SLF001 - the dispatch table is the source of truth
    # The Content-Type map lives in domain.document_formats (BL-193); read it
    # through the public accessor so this collector cannot pin a private name.
    for ext, fn in sorted(extractors.items()):
        source_file, source_line = locate(fn)
        result.features.append(
            Feature(
                id=f"ingest.format.{ext}",
                name=ext,
                category="ingest.format",
                surface="Ingestion",
                description=first_line(fn.__doc__),
                source_file=source_file,
                source_line=source_line,
                signature=extraction.content_type_for_extension(ext),
                signals=["runtime-registry"],
                probes=[f'"{ext}"', fn.__name__],
            )
        )
    return result


def collect_annotation() -> CollectorResult:
    """Linguistic annotation layers and the language models that can produce them."""
    result = CollectorResult("annotation", "runtime:spacy-capability-registry")
    caps = importlib.import_module("doc_ray.adapters.annotation_capabilities")
    caps_file, _ = locate(caps)
    for layer in caps.SUPPORTED_SPACY_LAYERS:
        result.features.append(
            Feature(
                id=f"annotation.layer.{layer}",
                name=layer,
                category="annotation.layer",
                surface="NLP annotation",
                description="",
                source_file=caps_file,
                source_line=grep_line(ROOT / caps_file, rf'"{layer}"'),
                signals=["runtime-registry"],
                probes=[layer],
            )
        )
    for capability in caps.spacy_capabilities():
        state = "installed" if capability.installed else "declared, model not installed"
        result.features.append(
            Feature(
                id=f"annotation.language.{capability.language}",
                name=capability.language,
                category="annotation.language",
                surface="NLP annotation",
                description=f"{capability.model} ({state})",
                source_file=caps_file,
                source_line=grep_line(ROOT / caps_file, rf'"{capability.language}":'),
                signature=f"{len(capability.supported_layers)} layers",
                signals=["runtime-registry"],
                probes=[capability.model, f'"{capability.language}"'],
                notes="" if capability.installed else "Model wheel absent in this environment.",
            )
        )
    return result


def collect_config_flags() -> CollectorResult:
    """Runtime-configurable behaviour, from the environment variable registry."""
    import yaml

    result = CollectorResult("config", "static:config/env.registry.yaml")
    registry = ROOT / "config" / "env.registry.yaml"
    data = yaml.safe_load(registry.read_text(encoding="utf-8")) or {}
    for entry in data.get("variables", []):
        key = str(entry.get("key", ""))
        if not key:
            continue
        result.features.append(
            Feature(
                id=f"config.variable.{key}",
                name=key,
                category="config.variable",
                surface=f"Configuration / {entry.get('group', 'other')}",
                description=first_line(entry.get("description", "")),
                source_file="config/env.registry.yaml",
                source_line=grep_line(registry, rf"^- key: {key}$"),
                signature=f"default={entry.get('default')!r}",
                signals=["static-declaration"],
                probes=[key],
                notes="secret" if entry.get("secret") else "",
            )
        )
    return result


def collect_operator_scripts() -> CollectorResult:
    """Operator-facing CLI tools in ``scripts/``, parsed without importing them."""
    result = CollectorResult("scripts", "static:ast-parse+module-docstring")
    for path in sorted((ROOT / "scripts").glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as exc:
            result.error = f"{path.name}: {exc}"
            continue
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        has_cli = "argparse" in names | attrs or "ArgumentParser" in names | attrs
        result.features.append(
            Feature(
                id=f"ops.script.{path.stem}",
                name=path.name,
                category="ops.script",
                surface="Operator tooling",
                description=first_line(ast.get_docstring(tree)),
                source_file=_relative(path),
                source_line=1,
                signature="argparse CLI" if has_cli else "script",
                signals=["static-declaration"],
                probes=[path.stem],
            )
        )
    return result


#: Every collector, in report order.
COLLECTORS = (
    collect_graphql,
    collect_mcp,
    collect_http_routes,
    collect_webapp_routes,
    collect_jobs,
    collect_ingestion_formats,
    collect_annotation,
    collect_config_flags,
    collect_operator_scripts,
)
