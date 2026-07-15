"""The resolver backend is selectable via DOCRAY_RESOLVER_BACKEND (anthropic|ollama)."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from doc_ray.adapters.resolver_factory import make_resolver


def _stub_anthropic(monkeypatch):
    # Constructing ClaudeResolver opens an anthropic.Anthropic client — stub the SDK.
    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        SimpleNamespace(Anthropic=lambda **kw: SimpleNamespace(messages=None)),
    )


def test_default_backend_is_anthropic(monkeypatch):
    monkeypatch.delenv("DOCRAY_RESOLVER_BACKEND", raising=False)
    _stub_anthropic(monkeypatch)
    assert type(make_resolver()).__name__ == "ClaudeResolver"


def test_backend_ollama_selected_by_env(monkeypatch):
    monkeypatch.setenv("DOCRAY_RESOLVER_BACKEND", "OLLAMA")  # case-insensitive
    resolver = make_resolver()
    assert type(resolver).__name__ == "OllamaResolver"
    assert resolver.name.startswith("ollama/")


def test_explicit_argument_overrides_env(monkeypatch):
    monkeypatch.setenv("DOCRAY_RESOLVER_BACKEND", "ollama")
    _stub_anthropic(monkeypatch)
    assert type(make_resolver("anthropic")).__name__ == "ClaudeResolver"


def test_unknown_backend_fails_closed():
    with pytest.raises(ValueError, match="unknown"):
        make_resolver("gpt-4")
