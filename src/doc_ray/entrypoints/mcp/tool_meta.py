"""Authoritative model-facing metadata for every live MCP tool.

The annotations are advisory hints, not authorization controls. GraphQL write
scope, billing entitlement, per-document ACL checks, and Postgres RLS remain the
authoritative enforcement boundary. ``openWorldHint`` describes the complete
tool operation, including work performed later by a queued job.
"""

from __future__ import annotations

from dataclasses import dataclass

from mcp.types import ToolAnnotations

from doc_ray.entrypoints.mcp import (
    compose,
    corpus,
    mention_confidence,
    mutations,
    mutations_surface,
    semantic,
    structure,
)


@dataclass(frozen=True)
class ToolMeta:
    """A tool's separate human label and all four MCP behavior hints."""

    title: str
    annotations: ToolAnnotations


_READ_TOOLS = (
    *corpus.TOOLS,
    *semantic.TOOLS,
    *structure.TOOLS,
    *mention_confidence.TOOLS,
    *compose.TOOLS,
)
_WRITE_TOOLS = (*mutations.TOOLS, *mutations_surface.TOOLS)
_ALL_TOOLS = (*_READ_TOOLS, *_WRITE_TOOLS)
_READ_NAMES = frozenset(tool.__name__ for tool in _READ_TOOLS)

# A destructive hint means that at least one valid invocation can remove or
# replace curated state. It does not imply that every invocation is destructive.
_DESTRUCTIVE_NAMES = frozenset(
    {
        "update_document_metadata",
        "correct_entity_mention",
        "confirm_document_classification",
        "reject_projection",
        "decide_seeds",
        "decide_document_metadata_suggestions",
        "decide_visual_captions",
        "remove_document_from_collection",
        "update_collection",
        "delete_collection",
        "demote_lens",
        "set_document_default_lens",
        "reject_allusion",
    }
)

# These calls create a new durable row or enqueue new work on each successful
# invocation. Remaining writes converge on the requested state or are no-ops.
_NON_IDEMPOTENT_NAMES = frozenset(
    {
        "ingest_document",
        "reannotate",
        "reextract_document",
        "annotate_document",
        "semantic_enrich_document",
        "embed_document_sentences",
        "canonicalize_document",
        "suggest_seeds",
        "suggest_document_metadata",
        "update_document_metadata",
        "bulk_tag_documents",
        "correct_entity_mention",
        "classify_document",
        "confirm_projection",
        "reject_projection",
        "seed_approved",
        "create_collection",
    }
)

# Newer intake and enrichment jobs cross the corpus boundary even though their
# MCP front doors only enqueue work. All other tools operate on the RLS corpus.
_OPEN_WORLD_NAMES = frozenset(
    {"ingest_document", "canonicalize_document", "suggest_seeds", "seed_approved"}
)

_TITLE_WORDS = {
    "id": "ID",
    "ids": "IDs",
    "kwic": "KWIC",
}


def _title(name: str) -> str:
    """Convert a snake-case tool name into its distinct human-facing label."""
    return " ".join(_TITLE_WORDS.get(word, word.capitalize()) for word in name.split("_"))


def _annotations(name: str) -> ToolAnnotations:
    """Build complete annotations from the audited behavior policy."""
    read_only = name in _READ_NAMES
    return ToolAnnotations(
        readOnlyHint=read_only,
        destructiveHint=name in _DESTRUCTIVE_NAMES,
        idempotentHint=read_only or name not in _NON_IDEMPOTENT_NAMES,
        openWorldHint=name in _OPEN_WORLD_NAMES,
    )


#: Complete name-indexed catalog. Construction from the live registries makes a
#: missing entry impossible; tests enforce the inverse so stale surplus entries
#: and duplicate names also fail loudly.
TOOL_META: dict[str, ToolMeta] = {
    tool.__name__: ToolMeta(title=_title(tool.__name__), annotations=_annotations(tool.__name__))
    for tool in _ALL_TOOLS
}


def meta_for(name: str) -> ToolMeta:
    """Return metadata for a registered tool, failing loudly for unknown names."""
    try:
        return TOOL_META[name]
    except KeyError as exc:
        raise KeyError(f"unregistered MCP tool metadata: {name}") from exc


def _assert_policy_names_are_live() -> None:
    """Reject policy typos during import instead of silently losing a hint."""
    live = frozenset(TOOL_META)
    policy = _DESTRUCTIVE_NAMES | _NON_IDEMPOTENT_NAMES | _OPEN_WORLD_NAMES
    if stale := policy - live:
        raise RuntimeError(f"MCP metadata policy names are not registered: {sorted(stale)!r}")


_assert_policy_names_are_live()

__all__ = ["TOOL_META", "ToolMeta", "meta_for"]
