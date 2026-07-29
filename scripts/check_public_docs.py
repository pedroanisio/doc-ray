#!/usr/bin/env python3
"""Gate public documentation against live code, version, and link contracts.

Checks:

* every GraphQL Query/Mutation root field is named in ``reference/api.md``;
* every fenced GraphQL example validates against the live schema;
* every registered MCP tool is named in ``reference/mcp.md``;
* every supported-format claim matches the central format registry;
* documented config keys and CLI commands have live implementations;
* every non-release page marked current targets the canonical app version;
* every versioned release note agrees with its filename; and
* every relative link and published ``/docs/`` route resolves offline.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import check_doc_links
from graphql import GraphQLError, parse, validate
from graphql import build_schema as build_graphql_schema

_ROOT = Path(__file__).resolve().parent.parent
_PUBLIC = _ROOT / "docs" / "public"
_REF = _PUBLIC / "reference"
_VERSION_FILE_RE = re.compile(r"^(\d+\.\d+\.\d+)\.md$")
_FORMAT_CLAIM_PATHS = (
    "README.md",
    "docs/public/how-to/ingest-a-document.md",
    "docs/public/troubleshooting/unsupported-document-type.md",
    "docs/public/concepts/corpus-and-documents.md",
    "docs/public/faq/index.md",
)

sys.path.insert(0, str(_ROOT / "src"))


def frontmatter(text: str) -> dict[str, str]:
    """Parse the scalar fields this gate needs from YAML frontmatter."""
    if not text.startswith("---\n"):
        return {}
    closing = text.find("\n---\n", 4)
    if closing < 0:
        return {}
    block = text[4:closing]
    values: dict[str, str] = {}
    for line in block.splitlines():
        match = re.match(r"^([a-zA-Z_][a-zA-Z0-9_-]*):\s*(.*?)\s*$", line)
        if match:
            values[match.group(1)] = match.group(2).strip("\"'")
    return values


def _canonical_version(root: Path = _ROOT) -> str:
    text = (root / "src" / "doc_ray" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if match is None:
        raise RuntimeError("cannot read doc_ray.__version__")
    return match.group(1)


def _schema_operations() -> set[str]:
    from doc_ray.entrypoints.api.schema import build_schema  # noqa: PLC0415

    sdl = build_schema().as_str()
    ops: set[str] = set()
    for type_name in ("Query", "Mutation"):
        match = re.search(rf"^type {type_name}[^{{]*\{{(.*?)^\}}", sdl, re.MULTILINE | re.DOTALL)
        if match is None:
            raise RuntimeError(f"no `type {type_name}` block in the schema SDL")
        ops.update(
            re.findall(r"^\s{2}([a-zA-Z_][a-zA-Z0-9_]*)\s*[(:]", match.group(1), re.MULTILINE)
        )
    return ops


def _mcp_tools(root: Path = _ROOT) -> set[str]:
    tools: set[str] = set()
    for path in sorted((root / "src" / "doc_ray" / "entrypoints" / "mcp").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            targets: list[str] = []
            value: ast.expr | None = None
            if isinstance(node, ast.Assign):
                targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
                value = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target.id]
                value = node.value
            if "TOOLS" in targets and isinstance(value, (ast.Tuple, ast.List)):
                tools.update(element.id for element in value.elts if isinstance(element, ast.Name))
    if not tools:
        raise RuntimeError("no TOOLS tuples found under entrypoints/mcp/")
    return tools


def _backticked(text: str) -> set[str]:
    return set(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", text))


def format_claim_problems(
    text: str,
    *,
    source: str,
    supported_names: tuple[str, ...],
    supported_extensions: frozenset[str],
) -> list[str]:
    """Return missing format names and extensions falsely advertised as supported."""
    normalized = re.sub(r"[-‐‑–—\s]+", " ", text.casefold())
    problems = [
        f"{source}: omits supported format {name}"
        for name in supported_names
        if re.sub(r"[-‐‑–—\s]+", " ", name.casefold()) not in normalized
    ]
    claim_paragraphs = [
        paragraph
        for paragraph in re.split(r"\n\s*\n", text)
        if re.search(r"\b(?:support(?:ed|s)?|ingest(?:s|ed|ion)?|inputs?)\b", paragraph, re.I)
    ]
    claimed_extensions = set(
        re.findall(r"`(\.[a-z0-9]+)`", "\n".join(claim_paragraphs), re.IGNORECASE)
    )
    problems.extend(
        f"{source}: advertises unsupported extension {extension}"
        for extension in sorted(claimed_extensions - supported_extensions)
    )
    return problems


def format_registry_problems(root: Path = _ROOT) -> list[str]:
    """Return prose format claims that disagree with the live format registry."""
    from doc_ray.domain.document_formats import (  # noqa: PLC0415
        SUPPORTED_EXTENSIONS,
        SUPPORTED_FORMAT_NAMES,
    )

    problems: list[str] = []
    for relative in _FORMAT_CLAIM_PATHS:
        problems.extend(
            format_claim_problems(
                (root / relative).read_text(encoding="utf-8"),
                source=relative,
                supported_names=SUPPORTED_FORMAT_NAMES,
                supported_extensions=SUPPORTED_EXTENSIONS,
            )
        )
    return problems


def graphql_example_problems(root: Path = _ROOT, *, text: str | None = None) -> list[str]:
    """Return parse/schema diagnostics for fenced public GraphQL examples."""
    from doc_ray.entrypoints.api.schema import build_schema  # noqa: PLC0415

    supplied_text = text is not None
    api_text = (
        text
        if text is not None
        else (root / "docs" / "public" / "reference" / "api.md").read_text(encoding="utf-8")
    )
    blocks = re.findall(r"```graphql\s*\n(.*?)```", api_text, re.IGNORECASE | re.DOTALL)
    problems: list[str] = []
    if not supplied_text and len(blocks) < 6:
        problems.append(f"reference/api.md: only {len(blocks)} GraphQL examples found")
    schema = build_graphql_schema(build_schema(debug=True).as_str())
    for index, block in enumerate(blocks, start=1):
        try:
            document = parse(block)
        except GraphQLError as exc:
            problems.append(f"reference/api.md GraphQL example {index}: {exc.message}")
            continue
        problems.extend(
            f"reference/api.md GraphQL example {index}: {error.message}"
            for error in validate(schema, document)
        )
    return problems


def reference_surface_problems(root: Path = _ROOT) -> list[str]:
    """Return API/MCP completeness and config/CLI phantom diagnostics."""
    reference = root / "docs" / "public" / "reference"
    problems: list[str] = []
    api_text = (reference / "api.md").read_text(encoding="utf-8")
    missing_ops = sorted(_schema_operations() - _backticked(api_text))
    if missing_ops:
        problems.append(
            f"reference/api.md: undocumented schema operations: {', '.join(missing_ops)}"
        )

    mcp_text = (reference / "mcp.md").read_text(encoding="utf-8")
    missing_tools = sorted(_mcp_tools(root) - _backticked(mcp_text))
    if missing_tools:
        problems.append(f"reference/mcp.md: undocumented MCP tools: {', '.join(missing_tools)}")

    registry_keys = set(
        re.findall(
            r"^- key: ([A-Z0-9_]+)",
            (root / "config" / "env.registry.yaml").read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    )
    config_text = (reference / "config.md").read_text(encoding="utf-8")
    documented_keys = set(re.findall(r"^### `([A-Z0-9_]+)`", config_text, re.MULTILINE))
    phantom_keys = sorted(documented_keys - registry_keys)
    if phantom_keys:
        problems.append(f"reference/config.md: unknown config keys: {', '.join(phantom_keys)}")

    cli_text = (reference / "cli.md").read_text(encoding="utf-8")
    documented_cmds = set(re.findall(r"^## `([a-z0-9_]+)`", cli_text, re.MULTILINE))
    entrypoints = {path.stem for path in (root / "src" / "doc_ray" / "entrypoints").glob("*.py")}
    phantom_cmds = sorted(documented_cmds - entrypoints)
    if phantom_cmds:
        problems.append(
            f"reference/cli.md: commands without entrypoints: {', '.join(phantom_cmds)}"
        )
    problems.extend(graphql_example_problems(root))
    return problems


def public_metadata_problems(root: Path = _ROOT) -> list[str]:
    """Return current non-release pages whose version metadata is stale."""
    canonical = _canonical_version(root)
    public = root / "docs" / "public"
    problems: list[str] = []
    for path in sorted(public.rglob("*.md")):
        if "release-notes" in path.relative_to(public).parts:
            continue
        metadata = frontmatter(path.read_text(encoding="utf-8"))
        if metadata.get("status") == "current" and metadata.get("applies_to") != canonical:
            problems.append(
                f"{path.relative_to(root)}: current page applies_to "
                f"{metadata.get('applies_to') or 'missing'}, expected {canonical}"
            )
    return problems


def release_note_identity_problems(root: Path = _ROOT) -> list[str]:
    """Return versioned release notes whose metadata/title identity is inconsistent."""
    notes = root / "docs" / "public" / "release-notes"
    problems: list[str] = []
    for path in sorted(notes.glob("*.md")):
        match = _VERSION_FILE_RE.fullmatch(path.name)
        if match is None:
            continue
        version = match.group(1)
        text = path.read_text(encoding="utf-8")
        metadata = frontmatter(text)
        heading = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        if metadata.get("applies_to") != version:
            problems.append(
                f"{path.relative_to(root)}: applies_to "
                f"{metadata.get('applies_to') or 'missing'}, expected {version}"
            )
        if version not in metadata.get("title", ""):
            problems.append(f"{path.relative_to(root)}: frontmatter title omits {version}")
        if heading is None or version not in heading.group(1):
            problems.append(f"{path.relative_to(root)}: H1 omits {version}")
    return problems


def public_link_problems(root: Path = _ROOT) -> list[str]:
    """Return unresolved relative links and ``/docs/`` routes in public pages."""
    paths = sorted((root / "docs" / "public").rglob("*.md"))
    return check_doc_links.find_link_problems(root, paths)


def main() -> int:
    """Run the complete public documentation gate."""
    problems = [
        *reference_surface_problems(),
        *format_registry_problems(),
        *public_metadata_problems(),
        *release_note_identity_problems(),
        *public_link_problems(),
    ]
    if problems:
        print("public documentation drift:", file=sys.stderr)
        print("\n".join(f"  - {problem}" for problem in problems), file=sys.stderr)
        return 1
    print(
        "public docs OK: live API/MCP surfaces and examples, supported formats, "
        "version metadata, release identities, and links are in sync"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
