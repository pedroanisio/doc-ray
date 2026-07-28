"""Unit tests for the Tier-3 VLM backend factory (no GPU, no live server).

Locks the selection contract: unset/empty env → Ollama (the historical default,
so existing deployments do not change behaviour), ``vllm`` → the
OpenAI-compatible adapter, anything else → a loud error, never a silent default.
"""

from __future__ import annotations

from typing import Any

import pytest

from doc_ray.adapters.vlm_factory import make_vlm
from doc_ray.adapters.vlm_ollama import OllamaVisionLanguageModel
from doc_ray.adapters.vlm_vllm import VLLMVisionLanguageModel


def test_default_is_ollama(monkeypatch: Any) -> None:
    monkeypatch.delenv("DOCRAY_VLM_BACKEND", raising=False)
    assert isinstance(make_vlm(), OllamaVisionLanguageModel)


def test_empty_env_is_ollama(monkeypatch: Any) -> None:
    monkeypatch.setenv("DOCRAY_VLM_BACKEND", "")
    assert isinstance(make_vlm(), OllamaVisionLanguageModel)


def test_env_selects_vllm(monkeypatch: Any) -> None:
    monkeypatch.setenv("DOCRAY_VLM_BACKEND", "vllm")
    assert isinstance(make_vlm(), VLLMVisionLanguageModel)


def test_explicit_arg_overrides_env(monkeypatch: Any) -> None:
    monkeypatch.setenv("DOCRAY_VLM_BACKEND", "ollama")
    assert isinstance(make_vlm("vllm"), VLLMVisionLanguageModel)


def test_unknown_backend_fails_closed(monkeypatch: Any) -> None:
    monkeypatch.setenv("DOCRAY_VLM_BACKEND", "gpt-in-a-box")
    with pytest.raises(ValueError, match="DOCRAY_VLM_BACKEND"):
        make_vlm()
