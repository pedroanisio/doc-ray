"""Unit tests for the Ollama VLM adapter (no GPU, no live model).

Mocks the HTTP layer to lock the request contract: a plain feature-report prompt
(not a JSON envelope), a token budget + temperature in ``options``, one base64
image, and the plain ``response`` returned as the caption. PALS: the model is
called image-only unless the caller deliberately conditions on evidence.
"""

from __future__ import annotations

import json
from typing import Any

from doc_ray.adapters import vlm_ollama
from doc_ray.adapters.vlm_ollama import DEFAULT_MODEL, OllamaVisionLanguageModel


class _FakeResp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _patch(
    monkeypatch: Any, captured: dict[str, Any], response: str = "Box A: foo. Note C."
) -> None:
    def fake_urlopen(req: Any, timeout: int | None = None) -> _FakeResp:
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp({"response": response})

    monkeypatch.setattr(vlm_ollama.urllib_request, "urlopen", fake_urlopen)


def test_caption_image_builds_feature_report_request(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    _patch(monkeypatch, captured, response="Box A: scalar replacement. Note C at top.")
    vlm = OllamaVisionLanguageModel(
        model="qwen3-vl:32b-instruct", host="http://h:11434", num_predict=500, temperature=0.1
    )

    out = vlm.caption_image(b"\x89PNG\r\n", "")

    assert out.caption == "Box A: scalar replacement. Note C at top."
    body = captured["body"]
    assert captured["url"] == "http://h:11434/api/generate"
    assert body["model"] == "qwen3-vl:32b-instruct"
    assert body["stream"] is False
    assert "format" not in body  # plain text, NOT a JSON envelope
    assert body["options"] == {"num_predict": 500, "temperature": 0.1}
    assert "no inference" in body["prompt"] and "token_budget: 500" in body["prompt"]
    assert len(body["images"]) == 1 and isinstance(body["images"][0], str)


def test_caption_image_is_image_only_by_default(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    _patch(monkeypatch, captured)
    OllamaVisionLanguageModel(host="http://h:11434").caption_image(b"img", "")
    assert "Context from the document" not in captured["body"]["prompt"]


def test_caption_image_conditions_only_when_evidence_supplied(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    _patch(monkeypatch, captured)
    OllamaVisionLanguageModel(host="http://h:11434").caption_image(b"img", "Figure 3 shows an A330")
    assert "Context from the document: Figure 3 shows an A330" in captured["body"]["prompt"]


def test_host_reads_the_canonical_docray_ollama_host(monkeypatch: Any) -> None:
    """DOCRAY_OLLAMA_HOST is the registered var; the VLM must honour it.

    The defect this pins: the compose overlay
    sets only ``DOCRAY_OLLAMA_HOST`` on the workers — the same var
    :mod:`doc_ray.adapters.resolver_ollama` reads — while this adapter looked at
    ``OLLAMA_HOST`` alone and therefore fell back to ``localhost`` inside the
    worker container, where nothing serves Ollama. Every Tier-3 caption job
    would have failed to connect.
    """
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setenv("DOCRAY_OLLAMA_HOST", "http://ollama:11434")
    captured: dict[str, Any] = {}
    _patch(monkeypatch, captured)

    OllamaVisionLanguageModel().caption_image(b"img", "")

    assert captured["url"] == "http://ollama:11434/api/generate"


def test_ollama_host_still_wins_for_ancillary_tooling(monkeypatch: Any) -> None:
    """OLLAMA_HOST (the Ollama CLI's own var) keeps precedence when both are set."""
    monkeypatch.setenv("DOCRAY_OLLAMA_HOST", "http://ollama:11434")
    monkeypatch.setenv("OLLAMA_HOST", "http://elsewhere:11434")
    captured: dict[str, Any] = {}
    _patch(monkeypatch, captured)

    OllamaVisionLanguageModel().caption_image(b"img", "")

    assert captured["url"] == "http://elsewhere:11434/api/generate"


def test_defaults_and_env_overrides(monkeypatch: Any) -> None:
    monkeypatch.delenv("DOCRAY_VLM_MODEL", raising=False)
    monkeypatch.delenv("DOCRAY_VLM_NUM_PREDICT", raising=False)
    monkeypatch.delenv("DOCRAY_VLM_TEMPERATURE", raising=False)
    assert OllamaVisionLanguageModel().model_id == DEFAULT_MODEL

    monkeypatch.setenv("DOCRAY_VLM_MODEL", "custom-vlm")
    monkeypatch.setenv("DOCRAY_VLM_NUM_PREDICT", "1234")
    captured: dict[str, Any] = {}
    _patch(monkeypatch, captured)
    OllamaVisionLanguageModel(host="http://h:11434").caption_image(b"img", "")
    assert captured["body"]["model"] == "custom-vlm"
    assert captured["body"]["options"]["num_predict"] == 1234
