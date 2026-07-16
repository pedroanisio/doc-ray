"""Adapter: an Ollama-served vision-language model (Prism Tier-3).

Reuses the Ollama HTTP scaffolding the Stage-3 resolver already uses, adding the
base64 ``images`` field the vision models need (the resolver payload has none).
Only runs where an Ollama host serves a vision model (the H200 worker); the
caption gate and orchestration are tested with a fake VLM instead.

Output is UNTRUSTED (PALS): the caller grounds and gates it — this adapter only
transports the model's words, it does not decide anything.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any, cast
from urllib import request as urllib_request

from doc_ray.ports.vlm import VisionLanguageModel, VLMOutput

_PROMPT = (
    "Describe this figure precisely for a technical index. Name any labels, "
    "numbers, entities, and chart/diagram type you can read. Be specific; do not "
    'guess. Reply as JSON: {"caption": str, "analysis": {...}}.'
)


class OllamaVisionLanguageModel(VisionLanguageModel):
    """Caption images via an Ollama vision model over HTTP."""

    def __init__(
        self,
        model: str | None = None,
        host: str | None = None,
        *,
        timeout_seconds: int = 120,
    ) -> None:
        """Configure host/model from args or DOCRAY_VLM_* / OLLAMA_HOST env."""
        self.model_id = model or os.environ.get("DOCRAY_VLM_MODEL", "llava")
        self.model_version = "ollama"
        self._host = (host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")
        self._timeout = timeout_seconds

    def caption_image(self, image_bytes: bytes, evidence_text: str) -> VLMOutput:
        """Return the model's caption + analysis (untrusted, JSON-decoded best-effort)."""
        payload = {
            "model": self.model_id,
            "prompt": f"{_PROMPT}\n\nThe document says about this figure: {evidence_text[:2000]}",
            "images": [base64.b64encode(image_bytes).decode("ascii")],
            "stream": False,
            "format": "json",
        }
        req = urllib_request.Request(
            f"{self._host}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"content-type": "application/json"},
        )
        with urllib_request.urlopen(req, timeout=self._timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return _parse_response(body.get("response", ""))


def _parse_response(response: str) -> VLMOutput:
    """Best-effort parse of the model's JSON response into a VLMOutput."""
    try:
        data = cast("dict[str, Any]", json.loads(response))
    except (json.JSONDecodeError, TypeError):
        return VLMOutput(caption=response.strip())
    caption = str(data.get("caption", "")).strip()
    analysis = data.get("analysis")
    return VLMOutput(caption=caption, analysis=analysis if isinstance(analysis, dict) else {})
