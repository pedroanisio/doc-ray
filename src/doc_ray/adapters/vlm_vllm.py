"""Adapter: a vLLM-served vision-language model over its OpenAI-compatible API.

vLLM does true continuous batching for multimodal requests, so it saturates a GPU
where Ollama serializes vision decode — the throughput path for a corpus-wide
Tier-3 caption backfill. The prompt is the same feature-report instruction the
Ollama adapter uses (transcribe visible features, no interpretation), so captions
remain verifiable by the Tier-3 grounding gate.

Output is UNTRUSTED (PALS): the caller grounds and gates it — this adapter only
transports the model's words, it does not decide anything.
"""

from __future__ import annotations

import base64
import io
import json
import os
from urllib import request as urllib_request

from doc_ray.adapters.extraction_common import env_float, env_int
from doc_ray.ports.vlm import VisionLanguageModel, VLMOutput

DEFAULT_MODEL = "qwen3-vl-8b"
DEFAULT_ENDPOINT = "http://localhost:8000/v1"
DEFAULT_NUM_PREDICT = 400
DEFAULT_TEMPERATURE = 0.1
#: Cap the longest image side before sending to the VLM. Full-resolution page
#: renders carry thousands of vision tokens; feature-reporting (reading labels)
#: needs far fewer, and prefill cost scales with vision tokens, so downscaling is
#: the dominant throughput lever for a corpus-wide pass. 0 disables downscaling.
DEFAULT_IMAGE_MAX_PX = 1024


def _prepare_image(image_bytes: bytes, max_px: int) -> tuple[str, str]:
    """Downscale (if needed) and encode; return (mime, base64). JPEG for size."""
    if max_px <= 0:
        return "image/png", base64.b64encode(image_bytes).decode("ascii")
    from PIL import Image

    with Image.open(io.BytesIO(image_bytes)) as im:
        rgb = im.convert("RGB")
        if max(rgb.size) > max_px:
            ratio = max_px / max(rgb.size)
            rgb = rgb.resize((max(1, int(rgb.width * ratio)), max(1, int(rgb.height * ratio))))
        out = io.BytesIO()
        rgb.save(out, format="JPEG", quality=85)
    return "image/jpeg", base64.b64encode(out.getvalue()).decode("ascii")


_PROMPT_TEMPLATE = (
    "describe this image. no inference, just report features.\ntoken_budget: {budget}."
)


class VLLMVisionLanguageModel(VisionLanguageModel):
    """Caption images via a vLLM OpenAI-compatible /chat/completions endpoint."""

    def __init__(
        self,
        model: str | None = None,
        endpoint: str | None = None,
        *,
        num_predict: int | None = None,
        temperature: float | None = None,
        timeout_seconds: int = 180,
    ) -> None:
        """Configure model/endpoint/decoding from args or DOCRAY_VLM_* env."""
        self.model_id = model or os.environ.get("DOCRAY_VLM_MODEL", DEFAULT_MODEL)
        self.model_version = "vllm"
        self._endpoint = (
            endpoint or os.environ.get("DOCRAY_VLM_ENDPOINT", DEFAULT_ENDPOINT)
        ).rstrip("/")
        self._num_predict = (
            num_predict
            if num_predict is not None
            else env_int(os.environ, "DOCRAY_VLM_NUM_PREDICT", DEFAULT_NUM_PREDICT)
        )
        self._temperature = (
            temperature
            if temperature is not None
            else env_float(os.environ, "DOCRAY_VLM_TEMPERATURE", DEFAULT_TEMPERATURE)
        )
        self._max_px = env_int(os.environ, "DOCRAY_VLM_IMAGE_MAX_PX", DEFAULT_IMAGE_MAX_PX)
        self._timeout = timeout_seconds

    def caption_image(self, image_bytes: bytes, evidence_text: str) -> VLMOutput:
        """Return the model's feature-report caption (untrusted, plain text)."""
        # Image-only by default (Prism Tier-3 passes no evidence) so the caption is
        # produced blind to the grounding text and cannot be gamed by echoing it.
        prompt = _PROMPT_TEMPLATE.format(budget=self._num_predict)
        if evidence_text.strip():
            prompt = f"{prompt}\n\nContext from the document: {evidence_text[:2000]}"
        mime, b64 = _prepare_image(image_bytes, self._max_px)
        data_url = f"data:{mime};base64,{b64}"
        payload = {
            "model": self.model_id,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "max_tokens": self._num_predict,
            "temperature": self._temperature,
        }
        req = urllib_request.Request(
            f"{self._endpoint}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"content-type": "application/json"},
        )
        with urllib_request.urlopen(req, timeout=self._timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        choices = body.get("choices") or [{}]
        content = (choices[0].get("message") or {}).get("content") or ""
        return VLMOutput(caption=str(content).strip())
