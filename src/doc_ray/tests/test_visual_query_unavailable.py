"""crossModalImageSearch must fail clean on hosts without the vision stack.

Regression for the MCP-visible leak ``No module named 'open_clip'``: the
GraphQL read path lazily imports the Tier-2 vision encoder on the API host,
but ``open_clip``/``torch`` are deliberately absent from the API image (they
belong to the GPU worker). Querying the field on such a host must surface a
typed, actionable "unavailable on this deployment" error — never a raw
``ModuleNotFoundError`` — so downstream agents can classify it and stop
retrying. No database or model weights are needed here.
"""

from __future__ import annotations

import asyncio
import importlib.util

import pytest

from doc_ray.entrypoints.api import visual_query


@pytest.fixture(autouse=True)
def _fresh_encoder_cache():
    visual_query._query_vision_encoder.cache_clear()
    yield
    visual_query._query_vision_encoder.cache_clear()


def _hide_vision_stack(monkeypatch):
    """Make the vision-stack modules look uninstalled, as on the API image."""
    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name, *args, **kwargs):
        if name in {"open_clip", "torch"}:
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)


def test_encoder_factory_raises_typed_error_when_stack_missing(monkeypatch):
    _hide_vision_stack(monkeypatch)
    with pytest.raises(visual_query.VisionStackUnavailableError) as err:
        visual_query._query_vision_encoder()
    message = str(err.value)
    assert "not installed" in message  # the stable marker the MCP layer classifies on
    assert "open_clip" in message  # names what is missing, actionable for operators
    assert "No module named" not in message  # the raw leak this guards against


def test_cross_modal_graphql_error_is_actionable_not_a_module_leak(monkeypatch):
    """The wire contract: GraphQL ``errors[].message`` is typed and clean."""
    from doc_ray.entrypoints.api.schema import build_schema

    _hide_vision_stack(monkeypatch)
    schema = build_schema()
    result = asyncio.run(
        schema.execute(
            'query { crossModalImageSearch(query: "diagram", limit: 1) { id } }',
            context_value=object(),  # never reached: the resolver fails before using it
        )
    )
    assert result.errors, "expected a GraphQL error when the vision stack is absent"
    message = result.errors[0].message
    assert "not installed" in message
    assert "No module named" not in message
