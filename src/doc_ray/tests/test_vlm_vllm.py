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
        captured["headers"] = dict(req.headers)
        return _FakeResp(
            {
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 1100, "completion_tokens": 210, "total_tokens": 1310},
            }
        )

    monkeypatch.setattr(vlm_vllm.urllib_request, "urlopen", fake_urlopen)


def test_chat_request_shape_and_response(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    _patch(monkeypatch, captured, content="A labelled diagram.")
    vlm = VLLMVisionLanguageModel(
        model="qwen3-vl-8b", endpoint="http://h:8011/v1", num_predict=300, temperature=0.1
    )

    out = vlm.caption_image(_png((100, 100)), "")

    assert out.caption == "A labelled diagram."
    assert out.analysis["usage"]["prompt_tokens"] == 1100  # billing telemetry rides along
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


def test_negative_temperature_omits_the_field(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    _patch(monkeypatch, captured)
    VLLMVisionLanguageModel(endpoint="http://h:8011/v1", temperature=-1).caption_image(
        _png((50, 50)), ""
    )
    assert "temperature" not in captured["body"]  # hosted models may reject explicit values


def test_api_key_sends_bearer_header(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    _patch(monkeypatch, captured)
    VLLMVisionLanguageModel(endpoint="http://h:8011/v1", api_key="do-secret").caption_image(
        _png((50, 50)), ""
    )
    assert captured["headers"]["Authorization"] == "Bearer do-secret"


def test_no_api_key_sends_no_auth_header(monkeypatch: Any) -> None:
    monkeypatch.delenv("DOCRAY_VLM_API_KEY", raising=False)
    captured: dict[str, Any] = {}
    _patch(monkeypatch, captured)
    VLLMVisionLanguageModel(endpoint="http://h:8011/v1").caption_image(_png((50, 50)), "")
    assert "Authorization" not in captured["headers"]


def test_api_key_from_env(monkeypatch: Any) -> None:
    monkeypatch.setenv("DOCRAY_VLM_API_KEY", "env-secret")
    captured: dict[str, Any] = {}
    _patch(monkeypatch, captured)
    VLLMVisionLanguageModel(endpoint="http://h:8011/v1").caption_image(_png((50, 50)), "")
    assert captured["headers"]["Authorization"] == "Bearer env-secret"


def _http_error(code: int) -> Any:
    import email.message
    import urllib.error

    return urllib.error.HTTPError("http://h", code, "err", email.message.Message(), io.BytesIO())


def test_transient_errors_retry_then_succeed(monkeypatch: Any) -> None:
    calls = {"n": 0}
    sleeps: list[float] = []
    monkeypatch.setattr(vlm_vllm.time, "sleep", sleeps.append)

    def flaky_urlopen(req: Any, timeout: int | None = None) -> _FakeResp:
        calls["n"] += 1
        if calls["n"] < 3:
            raise _http_error(500 if calls["n"] == 1 else 429)
        return _FakeResp({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(vlm_vllm.urllib_request, "urlopen", flaky_urlopen)
    out = VLLMVisionLanguageModel(endpoint="http://h:8011/v1").caption_image(_png((50, 50)), "")
    assert out.caption == "ok"
    assert calls["n"] == 3 and len(sleeps) == 2  # two failures, two backoffs


def test_non_transient_error_raises_immediately(monkeypatch: Any) -> None:
    calls = {"n": 0}
    monkeypatch.setattr(vlm_vllm.time, "sleep", lambda s: None)

    def bad_request(req: Any, timeout: int | None = None) -> _FakeResp:
        calls["n"] += 1
        raise _http_error(400)

    monkeypatch.setattr(vlm_vllm.urllib_request, "urlopen", bad_request)
    import pytest

    with pytest.raises(Exception, match="400"):
        VLLMVisionLanguageModel(endpoint="http://h:8011/v1").caption_image(_png((50, 50)), "")
    assert calls["n"] == 1  # no retries on a permanent error


def test_retries_exhaust_then_raise(monkeypatch: Any) -> None:
    calls = {"n": 0}
    monkeypatch.setattr(vlm_vllm.time, "sleep", lambda s: None)

    def always_503(req: Any, timeout: int | None = None) -> _FakeResp:
        calls["n"] += 1
        raise _http_error(503)

    monkeypatch.setattr(vlm_vllm.urllib_request, "urlopen", always_503)
    import pytest

    with pytest.raises(Exception, match="503"):
        VLLMVisionLanguageModel(endpoint="http://h:8011/v1").caption_image(_png((50, 50)), "")
    assert calls["n"] == 4  # initial + one per backoff step
