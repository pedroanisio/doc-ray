"""MCP tool metadata is complete, truthful, and projected by ``tools/list``."""

from __future__ import annotations

import anyio
import pytest

from doc_ray.entrypoints.mcp import (
    compose,
    corpus,
    mention_confidence,
    mutations,
    mutations_surface,
    semantic,
    structure,
    tool_meta,
)
from doc_ray.entrypoints.mcp.server import build_server
from doc_ray.entrypoints.mcp.tool_meta import TOOL_META, meta_for

_READ_TOOLS = (
    *corpus.TOOLS,
    *semantic.TOOLS,
    *structure.TOOLS,
    *mention_confidence.TOOLS,
    *compose.TOOLS,
)
_WRITE_TOOLS = (*mutations.TOOLS, *mutations_surface.TOOLS)
_ALL_TOOLS = (*_READ_TOOLS, *_WRITE_TOOLS)

_DESTRUCTIVE_WRITE_NAMES = {
    "update_document_metadata",
    "correct_entity_mention",
    "confirm_document_classification",
    "reject_projection",
    "decide_seeds",
    "decide_document_metadata_suggestions",
    "decide_visual_captions",
    # A retraction replaces curated state (a published caption) and re-running
    # it converges rather than repeating, so: destructive + idempotent.
    "retract_visual_caption",
    "remove_document_from_collection",
    "update_collection",
    "delete_collection",
    "demote_lens",
    "set_document_default_lens",
    "reject_allusion",
}

_NON_IDEMPOTENT_WRITE_NAMES = {
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

_OPEN_WORLD_WRITE_NAMES = {
    "ingest_document",
    "canonicalize_document",
    "suggest_seeds",
    "seed_approved",
}


def test_metadata_catalog_has_bidirectional_live_registry_coverage():
    names = {tool.__name__ for tool in _ALL_TOOLS}
    assert set(TOOL_META) == names
    assert all(meta_for(name) is TOOL_META[name] for name in names)


def test_titles_are_separate_human_labels_not_descriptions():
    descriptions = {(tool.__doc__ or "").strip() for tool in _ALL_TOOLS}
    titles = [meta.title for meta in TOOL_META.values()]
    assert all(title.strip() for title in titles)
    assert len(titles) == len(set(titles))
    assert not (set(titles) & descriptions)
    assert all(meta.annotations.title is None for meta in TOOL_META.values())


def test_read_tools_have_safe_complete_annotations():
    for tool in _READ_TOOLS:
        hints = meta_for(tool.__name__).annotations
        assert hints.readOnlyHint is True
        assert hints.destructiveHint is False
        assert hints.idempotentHint is True
        assert hints.openWorldHint is False


def test_write_tools_have_explicit_destructive_and_idempotency_policy():
    for tool in _WRITE_TOOLS:
        name = tool.__name__
        hints = meta_for(name).annotations
        assert hints.readOnlyHint is False
        assert hints.destructiveHint is (name in _DESTRUCTIVE_WRITE_NAMES)
        assert hints.idempotentHint is (name not in _NON_IDEMPOTENT_WRITE_NAMES)


def test_only_jobs_that_can_reach_external_sources_are_open_world():
    actual = {
        tool.__name__ for tool in _WRITE_TOOLS if meta_for(tool.__name__).annotations.openWorldHint
    }
    assert actual == _OPEN_WORLD_WRITE_NAMES


def test_unknown_tool_metadata_fails_loudly():
    with pytest.raises(KeyError, match="unregistered MCP tool"):
        meta_for("not_a_tool")


def test_policy_typos_fail_during_catalog_validation(monkeypatch):
    monkeypatch.setattr(tool_meta, "_DESTRUCTIVE_NAMES", frozenset({"not_a_tool"}))
    with pytest.raises(RuntimeError, match="not_a_tool"):
        tool_meta._assert_policy_names_are_live()


def test_tools_list_projects_every_title_and_annotation_hint():
    listed = {tool.name: tool for tool in anyio.run(build_server(enable_write=True).list_tools)}
    assert set(listed) == set(TOOL_META)
    for name, tool in listed.items():
        expected = meta_for(name)
        assert tool.title == expected.title
        assert tool.annotations == expected.annotations
        assert tool.annotations is not None
        assert all(
            hint is not None
            for hint in (
                tool.annotations.readOnlyHint,
                tool.annotations.destructiveHint,
                tool.annotations.idempotentHint,
                tool.annotations.openWorldHint,
            )
        )
