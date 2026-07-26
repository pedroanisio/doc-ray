#!/usr/bin/env python3
"""Gate the public reference docs against their live sources (D-034 spirit).

docs/documentation-governance.md requires published reference to "match its
generated source"; docs/public/reference/ drifted five releases behind within
a day of landing because nothing checked it. This gate makes the highest-value
subset of that promise mechanical:

  * ``api.md``      — completeness: every Query/Mutation field in the live
                      Strawberry schema must be named (backticked) in the doc.
  * ``mcp.md``      — completeness: every MCP tool registered in a ``TOOLS``
                      tuple under ``src/doc_ray/entrypoints/mcp/`` must be
                      named (backticked) in the doc.
  * ``config.md``   — no phantoms: every ``### `KEY``` heading must exist in
                      ``config/env.registry.yaml``. Completeness is *not*
                      checked: the doc is a curated public subset by contract.
  * ``cli.md``      — no phantoms: every ``## `name``` heading must exist as
                      ``src/doc_ray/entrypoints/<name>.py``.

Phantom detection for api.md/mcp.md prose is deliberately out of scope (a
backticked word in prose is not reliably an operation name); completeness is
the direction that lies to users by omission.

Exit code 0 when every check passes, 1 otherwise.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_REF = _ROOT / "docs" / "public" / "reference"

sys.path.insert(0, str(_ROOT / "src"))


def _schema_operations() -> set[str]:
    from doc_ray.entrypoints.api.schema import build_schema  # noqa: PLC0415

    sdl = build_schema().as_str()
    ops: set[str] = set()
    for type_name in ("Query", "Mutation"):
        match = re.search(rf"^type {type_name}[^{{]*\{{(.*?)^\}}", sdl, re.MULTILINE | re.DOTALL)
        if match is None:
            raise SystemExit(f"fatal: no `type {type_name}` block in the schema SDL")
        ops.update(
            re.findall(r"^\s{2}([a-zA-Z_][a-zA-Z0-9_]*)\s*[(:]", match.group(1), re.MULTILINE)
        )
    return ops


def _mcp_tools() -> set[str]:
    tools: set[str] = set()
    for path in sorted((_ROOT / "src" / "doc_ray" / "entrypoints" / "mcp").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
                value = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target.id]
                value = node.value
            else:
                continue
            if "TOOLS" in targets and isinstance(value, (ast.Tuple, ast.List)):
                tools.update(el.id for el in value.elts if isinstance(el, ast.Name))
    if not tools:
        raise SystemExit("fatal: no TOOLS tuples found under entrypoints/mcp/")
    return tools


def _backticked(text: str) -> set[str]:
    return set(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", text))


def main() -> int:
    problems: list[str] = []

    api_text = (_REF / "api.md").read_text(encoding="utf-8")
    missing_ops = sorted(_schema_operations() - _backticked(api_text))
    if missing_ops:
        problems.append(
            "  - docs/public/reference/api.md: schema operations undocumented: "
            + ", ".join(missing_ops)
        )

    mcp_text = (_REF / "mcp.md").read_text(encoding="utf-8")
    missing_tools = sorted(_mcp_tools() - _backticked(mcp_text))
    if missing_tools:
        problems.append(
            "  - docs/public/reference/mcp.md: registered MCP tools undocumented: "
            + ", ".join(missing_tools)
        )

    registry_keys = set(
        re.findall(
            r"^- key: ([A-Z0-9_]+)",
            (_ROOT / "config" / "env.registry.yaml").read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    )
    config_text = (_REF / "config.md").read_text(encoding="utf-8")
    documented_keys = set(re.findall(r"^### `([A-Z0-9_]+)`", config_text, re.MULTILINE))
    phantom_keys = sorted(documented_keys - registry_keys)
    if phantom_keys:
        problems.append(
            "  - docs/public/reference/config.md: keys not in config/env.registry.yaml: "
            + ", ".join(phantom_keys)
        )

    cli_text = (_REF / "cli.md").read_text(encoding="utf-8")
    documented_cmds = set(re.findall(r"^## `([a-z0-9_]+)`", cli_text, re.MULTILINE))
    entrypoints = {p.stem for p in (_ROOT / "src" / "doc_ray" / "entrypoints").glob("*.py")}
    phantom_cmds = sorted(documented_cmds - entrypoints)
    if phantom_cmds:
        problems.append(
            "  - docs/public/reference/cli.md: commands with no entrypoint module: "
            + ", ".join(phantom_cmds)
        )

    if problems:
        print("public reference docs drift from live sources:", file=sys.stderr)
        print("\n".join(problems), file=sys.stderr)
        return 1

    print(
        "public docs OK: api.md covers the GraphQL surface, mcp.md covers all "
        "registered tools, config.md/cli.md have no phantoms"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
