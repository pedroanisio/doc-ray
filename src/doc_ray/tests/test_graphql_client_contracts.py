"""Regression guards for hand-written GraphQL client documents."""

from __future__ import annotations

import ast
from pathlib import Path

from graphql import parse, validate

from doc_ray.entrypoints.api.schema import build_schema

_ROOT = Path(__file__).resolve().parents[3]
_WEBAPP_GRAPHQL_CLIENT_FILES = tuple(
    str(path.relative_to(_ROOT)) for path in sorted((_ROOT / "webapp").glob("*.py"))
)
_MCP_GRAPHQL_CLIENT_FILES = ("src/doc_ray/entrypoints/mcp/corpus.py",)


def _literal_graphql_documents(rel_paths: tuple[str, ...]) -> dict[str, str]:
    documents: dict[str, str] = {}
    for rel_path in rel_paths:
        tree = ast.parse((_ROOT / rel_path).read_text(encoding="utf-8"), filename=rel_path)
        for index, document in enumerate(_graphql_literals(tree), start=1):
            documents[f"{rel_path}#{index}"] = document
    return documents


def _literal_string(node: ast.AST) -> str | None:
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, str) else None


def _record_if_graphql(node: ast.AST, documents: list[str]) -> None:
    value = _literal_string(node)
    if value is not None and value.lstrip().startswith(("query", "mutation")):
        documents.append(value)


def _graphql_literals(tree: ast.AST) -> list[str]:
    documents: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.args and _is_gql_call(node):
            _record_if_graphql(node.args[0], documents)
        elif isinstance(node, ast.Dict):
            _record_graphql_dict_values(node, documents)
    return documents


def _is_gql_call(node: ast.Call) -> bool:
    return (
        isinstance(node.func, ast.Name)
        and node.func.id == "gql"
        or isinstance(node.func, ast.Attribute)
        and node.func.attr == "gql"
    )


def _record_graphql_dict_values(node: ast.Dict, documents: list[str]) -> None:
    for key, value in zip(node.keys, node.values, strict=True):
        if _literal_string(key) in {"query", "mutation"}:
            _record_if_graphql(value, documents)


def _assert_documents_validate(documents: dict[str, str]) -> None:
    schema = build_schema()._schema
    failures: dict[str, list[str]] = {}
    for name, document in documents.items():
        errors = validate(schema, parse(document))
        if errors:
            failures[name] = [str(error) for error in errors]
    assert not failures


def test_webapp_graphql_documents_validate_against_live_schema():
    """Flask webapp GraphQL mutations must stay valid against Strawberry SDL."""
    documents = _literal_graphql_documents(_WEBAPP_GRAPHQL_CLIENT_FILES)

    assert len(documents) >= 19, (
        f"only {len(documents)} webapp GraphQL documents checked — the scanner regressed"
    )
    _assert_documents_validate(documents)


def test_webapp_graphql_guard_discovers_every_operation_module():
    """Regression: a new webapp GraphQL module must join the contract guard automatically."""
    operation_modules = {
        str(path.relative_to(_ROOT))
        for path in sorted((_ROOT / "webapp").glob("*.py"))
        if _graphql_literals(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    }

    assert set(_WEBAPP_GRAPHQL_CLIENT_FILES) >= operation_modules


def test_mcp_graphql_documents_validate_against_live_schema():
    """MCP read-tool queries must stay valid against Strawberry SDL."""
    documents = _literal_graphql_documents(_MCP_GRAPHQL_CLIENT_FILES)

    assert documents, "MCP GraphQL contract guard found no operation strings"
    _assert_documents_validate(documents)
