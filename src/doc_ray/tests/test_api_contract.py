"""Static GraphQL driving-adapter contract tests."""

from __future__ import annotations

import inspect
from pathlib import Path

from doc_ray.adapters import persistence_canonical_read, persistence_layers_read
from doc_ray.adapters import persistence_read as read
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


def test_graphql_exposes_reextract_backfill_surface():
    sdl = build_schema().as_str()

    assert "hasPositionData" in sdl
    assert "reextractDocument(" in sdl
    assert "reextractDocuments(" in sdl


def test_graphql_exposes_document_lifecycle_surface():
    sdl = build_schema().as_str()

    assert "lifecycleState" in sdl
    assert "archivedAt" in sdl
    assert "deletedAt" in sdl
    assert "archiveDocument(" in sdl
    assert "restoreDocument(" in sdl


def test_graphql_exposes_document_metadata_suggestion_surface():
    sdl = build_schema().as_str()

    assert "documentMetadataSuggestions(" in sdl
    assert "suggestDocumentMetadata(" in sdl
    assert "decideDocumentMetadataSuggestions(" in sdl
    assert "confidenceBand" in sdl


def test_graphql_exposes_document_collections_surface():
    sdl = build_schema().as_str()

    assert "collections(" in sdl
    assert "collection(" in sdl
    assert "createCollection(" in sdl
    assert "addDocumentToCollection(" in sdl
    assert "removeDocumentFromCollection(" in sdl
    assert "type DocumentCollection" in sdl
    assert "collections(" in sdl[sdl.index("type Document "):]


def test_graphql_job_type_exposes_queue_timing_and_attempts():
    sdl = build_schema().as_str()

    assert "createdAt" in sdl
    assert "startedAt" in sdl
    assert "finishedAt" in sdl
    assert "attempts" in sdl
    assert "maxAttempts" in sdl


def test_graphql_query_resolvers_delegate_to_read_adapter_without_sql():
    source = Path(query_mod.__file__).read_text()

    assert "from doc_ray.adapters import persistence_read as read" in source
    for fragment in ("SELECT ", "INSERT ", "UPDATE ", "DELETE ", ".execute("):
        assert fragment not in source
    for delegate in (
        "read.list_documents",
        "read.get_document",
        "read.list_collections",
        "read.get_collection",
        "read.search_sentences",
        "read.fulltext_search",
        "read.list_lexemes",
        "read.list_entities",
        "read.entity_frequency",
    ):
        assert delegate in source


def test_entity_and_chunk_reads_never_rejoin_document_content():
    """Regression guard for the entity_frequency/list_entities incident.

    entity_mention.surface and chunk.surface (migration 038) exist precisely so
    these reads never again reconstruct surface text via
    substring(document_content.full_text) at query time — a join that, without
    a documentId filter, forced an unbounded full-corpus scan and pegged every
    core for minutes. If one of these functions is edited to reintroduce
    ``document_content``, this test must fail.
    """
    for fn in (
        read.list_entities,
        read.entity_frequency,
        persistence_layers_read.list_chunks,
        persistence_layers_read.list_entity_links,
        persistence_canonical_read.list_unresolved_mentions,
    ):
        body = inspect.getsource(fn)
        assert "document_content" not in body, f"{fn.__qualname__} rejoined document_content"
        assert "substring(" not in body, f"{fn.__qualname__} reintroduced substring extraction"


def test_graphql_resolver_exceptions_are_logged_server_side(caplog):
    """BL-115: a resolver exception must be logged server-side, not just returned
    to the client — build_schema() must not silence Strawberry's default
    process_errors -> strawberry.execution logger integration."""
    from graphql import GraphQLError

    schema = build_schema()
    err = GraphQLError("boom", original_error=RuntimeError("boom"))
    with caplog.at_level("ERROR", logger="strawberry.execution"):
        schema.process_errors([err], execution_context=None)
    assert any("boom" in r.getMessage() for r in caplog.records)
