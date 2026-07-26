"""Drift guard: ``docs/mcp-service.md`` must match the live MCP tool registry (H2).

The tool counts and per-tool tables in ``docs/mcp-service.md`` are a hand-maintained
mirror of the tool tuples registered in :mod:`doc_ray.entrypoints.mcp`. Nothing
asserted the two agreed, so the doc silently drifted — an adversarial audit found
every count wrong and one write tool (``decide_visual_captions``) undocumented.
These guards tie the prose to the registry: every registered tool must be
documented, and every stated count must equal the registry cardinality. (Drift-map
finding H2.)
"""

from __future__ import annotations

import re
from pathlib import Path

from doc_ray.entrypoints.mcp import (
    compose,
    corpus,
    mutations,
    prompts,
    resources,
    semantic,
    structure,
)

_ROOT = Path(__file__).resolve().parents[3]
_DOC = _ROOT / "docs" / "mcp-service.md"

#: Read-tool groups keyed by the module that owns them (write tools are separate).
_READ_GROUPS = {
    "corpus": corpus.TOOLS,
    "semantic": semantic.TOOLS,
    "structure": structure.TOOLS,
    "compose": compose.TOOLS,
}


def _names(tools: object) -> list[str]:
    return [tool.__name__ for tool in tools]  # type: ignore[attr-defined]


def _all_tool_names() -> set[str]:
    read = {name for group in _READ_GROUPS.values() for name in _names(group)}
    return read | set(_names(mutations.TOOLS))


def _doc() -> str:
    return _DOC.read_text(encoding="utf-8")


def test_every_registered_mcp_tool_is_documented():
    """A new or renamed MCP tool must be added to mcp-service.md."""
    doc = _doc()
    missing = sorted(
        name for name in _all_tool_names() if not re.search(rf"\b{re.escape(name)}\b", doc)
    )
    assert not missing, f"MCP tools registered but undocumented in mcp-service.md: {missing}"


def test_mcp_service_doc_counts_match_the_registry():
    """Every tool count stated in the doc must equal the registry cardinality."""
    flat = re.sub(r"\s+", " ", _doc())
    read_total = sum(len(group) for group in _READ_GROUPS.values())
    expected = {
        r"\*\*(\d+) read tools\*\*": read_total,
        r"\((\d+) from `entrypoints/mcp/corpus\.py`": len(corpus.TOOLS),
        r"(\d+) structure tools from": len(structure.TOOLS),
        r"(\d+) composition-grounding tools from": len(compose.TOOLS),
        r"(\d+) semantic-search tool": len(semantic.TOOLS),
        r"\*\*(\d+) opt-in write tools\*\*": len(mutations.TOOLS),
        r"(\d+) user-selectable workflows": len(prompts.PROMPTS),
    }
    wrong: dict[str, tuple[int | None, int]] = {}
    for pattern, want in expected.items():
        match = re.search(pattern, flat)
        got = int(match.group(1)) if match else None
        if got != want:
            wrong[pattern] = (got, want)
    assert not wrong, f"mcp-service.md counts disagree with the registry (got, want): {wrong}"


def test_mcp_resource_counts_match_the_registry():
    """The listable + template split stated in the doc must sum to RESOURCES."""
    flat = re.sub(r"\s+", " ", _doc())
    listable = re.search(r"(\d+) listable ", flat)
    templates = re.search(r"(\d+) drill-down templates", flat)
    assert listable and templates, "could not parse the resource split from mcp-service.md"
    assert int(listable.group(1)) + int(templates.group(1)) == len(resources.RESOURCES)


def test_tool_registry_enumeration_is_nonvacuous():
    """Guard the guard: a broken registry import must not make the checks pass on nothing."""
    names = _all_tool_names()
    # Concrete floor spanning read + write; `decide_visual_captions` is the exact
    # tool the audit found undocumented, so it earns a permanent place here.
    assert {"search_sentences", "reannotate", "decide_visual_captions"} <= names
