"""semanticSearchSentences must fail clean on hosts without the sentence stack.

The GraphQL read path lazily imports the sentence-transformers encoder on the API
host, but that stack is deliberately absent from the API image (like the vision
stack). Querying the field on such a host must surface a typed, actionable "not
installed" error — never a raw ``ModuleNotFoundError`` — so downstream agents can
classify it and stop retrying. No database or model weights are needed here.
"""

from __future__ import annotations

import asyncio
import importlib.util

import pytest

from doc_ray.adapters.text_encoder_sentence_transformers import SemanticStackUnavailableError
from doc_ray.entrypoints.api import semantic_query


@pytest.fixture(autouse=True)
def _fresh_encoder_cache():
    semantic_query._query_text_encoder.cache_clear()
    yield
    semantic_query._query_text_encoder.cache_clear()


def _hide_semantic_stack(monkeypatch):
    """Make the sentence-transformers module look uninstalled, as on the API image."""
    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name, *args, **kwargs):
        if name == "sentence_transformers":
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)


def test_encoder_factory_raises_typed_error_when_stack_missing(monkeypatch):
    _hide_semantic_stack(monkeypatch)
    with pytest.raises(SemanticStackUnavailableError) as err:
        semantic_query._query_text_encoder()
    message = str(err.value)
    assert "not installed" in message  # stable marker the MCP layer classifies on
    assert "sentence_transformers" in message  # names what is missing
    assert "No module named" not in message  # the raw leak this guards against


def test_semantic_search_graphql_error_is_actionable_not_a_module_leak(monkeypatch):
    """The wire contract: GraphQL ``errors[].message`` is typed and clean."""
    from doc_ray.entrypoints.api.schema import build_schema

    _hide_semantic_stack(monkeypatch)
    schema = build_schema()
    result = asyncio.run(
        schema.execute(
            'query { semanticSearchSentences(query: "photosynthesis", limit: 1) { sentenceId } }',
            context_value=object(),  # never reached: the resolver fails before using it
        )
    )
    assert result.errors, "expected a GraphQL error when the semantic stack is absent"
    message = result.errors[0].message
    assert "not installed" in message
    assert "No module named" not in message
