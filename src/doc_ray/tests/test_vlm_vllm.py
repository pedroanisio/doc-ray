"""Unit tests for the vLLM VLM adapter (no GPU, no live server).

Mocks the HTTP layer to lock the OpenAI /chat/completions contract: a feature-
report text part + an image_url data part, decoding params, downscaling of large
images (the throughput lever), and plain-text extraction from the choice.
"""

from __future__ import annotations

import base64
import io
import json
from typing import Any

from PIL import Image

from doc_ray.adapters import vlm_vllm
from doc_ray.adapters.vlm_vllm import DEFAULT_MODEL, VLLMVisionLanguageModel


class _FakeResp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _png(size: tuple[int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (40, 90, 160)).save(buf, "PNG")
    return buf.getvalue()


def _patch(monkeypatch: Any, captured: dict[str, Any], content: str = "A flowchart.") -> None:
    def fake_urlopen(req: Any, timeout: int | None = None) -> _FakeResp:
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp({"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr(vlm_vllm.urllib_request, "urlopen", fake_urlopen)


def test_chat_request_shape_and_response(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    _patch(monkeypatch, captured, content="A labelled diagram.")
    vlm = VLLMVisionLanguageModel(
        model="qwen3-vl-8b", endpoint="http://h:8011/v1", num_predict=300, temperature=0.1
    )

    out = vlm.caption_image(_png((100, 100)), "")

    assert out.caption == "A labelled diagram."
    body = captured["body"]
    assert captured["url"] == "http://h:8011/v1/chat/completions"
    assert body["model"] == "qwen3-vl-8b"
    assert body["max_tokens"] == 300 and body["temperature"] == 0.1
    content = body["messages"][0]["content"]
    kinds = {part["type"] for part in content}
    assert kinds == {"text", "image_url"}
    text_part = next(p["text"] for p in content if p["type"] == "text")
    assert "no inference" in text_part


def test_large_image_is_downscaled_to_jpeg(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    _patch(monkeypatch, captured)
    VLLMVisionLanguageModel(endpoint="http://h:8011/v1", num_predict=200).caption_image(
        _png((4000, 3000)), ""
    )
    url = next(
        p["image_url"]["url"]
        for p in captured["body"]["messages"][0]["content"]
        if p["type"] == "image_url"
    )
    assert url.startswith("data:image/jpeg;base64,")
    decoded = base64.b64decode(url.split(",", 1)[1])
    with Image.open(io.BytesIO(decoded)) as im:
        assert max(im.size) <= 1024  # downscaled below the prefill-cost cap


def test_defaults(monkeypatch: Any) -> None:
    monkeypatch.delenv("DOCRAY_VLM_MODEL", raising=False)
    monkeypatch.delenv("DOCRAY_VLM_ENDPOINT", raising=False)
    assert VLLMVisionLanguageModel().model_id == DEFAULT_MODEL
