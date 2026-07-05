"""Static GraphQL driving-adapter contract tests."""

from __future__ import annotations

from pathlib import Path

from doc_ray.entrypoints.api import query as query_mod
from doc_ray.entrypoints.api.schema import build_schema


def test_graphql_query_root_exposes_committed_read_surface():
    sdl = build_schema().as_str()

    for field in (
        "documents(",
        "document(",
        "searchSentences(",
        "fullTextSearch(",
        "lexeme(",
        "lexemes(",
        "entities(",
        "entityFrequency(",
        "jobs(",
    ):
        assert field in sdl


def test_graphql_query_resolvers_delegate_to_read_adapter_without_sql():
    source = Path(query_mod.__file__).read_text()

    assert "from doc_ray.adapters import persistence_read as read" in source
    for fragment in ("SELECT ", "INSERT ", "UPDATE ", "DELETE ", ".execute("):
        assert fragment not in source
    for delegate in (
        "read.list_documents",
        "read.get_document",
        "read.search_sentences",
        "read.fulltext_search",
        "read.list_lexemes",
        "read.list_entities",
        "read.entity_frequency",
    ):
        assert delegate in source
