"""Adapter: an Ollama-served vision-language model (Prism Tier-3).

Reuses the Ollama HTTP scaffolding the Stage-3 resolver already uses, adding the
base64 ``images`` field the vision models need. The prompt asks for a
feature-report — a literal transcription of what is visible (labels, numbers,
shapes, arrows), with no interpretation — because a faithful transcription is
exactly what the Tier-3 grounding gate can verify against the document's own text
and against OCR of the crop. The model is an *Instruct* VLM (e.g.
``qwen3-vl:32b-instruct``): no hidden reasoning, so the whole token budget goes
to the description.

Output is UNTRUSTED (PALS): the caller grounds and gates it — this adapter only
transports the model's words, it does not decide anything.
"""

from __future__ import annotations

import base64
import json
import os
from urllib import request as urllib_request

from doc_ray.adapters.extraction_common import env_float, env_int
from doc_ray.ports.vlm import VisionLanguageModel, VLMOutput

#: The default VLM: the most capable open vision model that fits a single H200
#: (the 235B flagship needs multiple GPUs). Instruct, not Thinking — so the token
#: budget produces the description, not hidden reasoning that empties the answer.
DEFAULT_MODEL = "qwen3-vl:32b-instruct"
#: Last-resort host when neither OLLAMA_HOST nor DOCRAY_OLLAMA_HOST is set.
DEFAULT_HOST = "http://localhost:11434"
DEFAULT_NUM_PREDICT = 768
DEFAULT_TEMPERATURE = 0.1

#: Feature-report instruction: transcribe visible features, do not interpret.
_PROMPT_TEMPLATE = (
    "describe this image. no inference, just report features.\ntoken_budget: {budget}."
)


class OllamaVisionLanguageModel(VisionLanguageModel):
    """Caption images via an Ollama vision model over HTTP."""

    def __init__(
        self,
        model: str | None = None,
        host: str | None = None,
        *,
        num_predict: int | None = None,
        temperature: float | None = None,
        timeout_seconds: int = 180,
    ) -> None:
        """Configure host/model/decoding from args or DOCRAY_VLM_* / *OLLAMA_HOST env.

        The host resolves ``OLLAMA_HOST`` (the Ollama CLI's own variable, so a
        shell already pointed at a server keeps working) then
        ``DOCRAY_OLLAMA_HOST`` — the registered variable the compose overlays
        actually set on the workers, and the one
        :mod:`doc_ray.adapters.resolver_ollama` reads. Reading only the former
        left this adapter on ``localhost`` inside the worker container.
        """
        self.model_id = model or os.environ.get("DOCRAY_VLM_MODEL", DEFAULT_MODEL)
        self.model_version = "ollama"
        self._host = (
            host
            or os.environ.get("OLLAMA_HOST")
            or os.environ.get("DOCRAY_OLLAMA_HOST")
            or DEFAULT_HOST
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
        self._timeout = timeout_seconds

    def caption_image(self, image_bytes: bytes, evidence_text: str) -> VLMOutput:
        """Return the model's feature-report caption (untrusted, plain text)."""
        # Prism Tier-3 calls this with NO evidence (image-only) so the caption is
        # produced blind to the grounding text and cannot be gamed by echoing it;
        # the evidence branch exists only for callers that deliberately condition.
        prompt = _PROMPT_TEMPLATE.format(budget=self._num_predict)
        if evidence_text.strip():
            prompt = f"{prompt}\n\nContext from the document: {evidence_text[:2000]}"
        payload = {
            "model": self.model_id,
            "prompt": prompt,
            "images": [base64.b64encode(image_bytes).decode("ascii")],
            "stream": False,
            "options": {"num_predict": self._num_predict, "temperature": self._temperature},
        }
        req = urllib_request.Request(
            f"{self._host}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"content-type": "application/json"},
        )
        with urllib_request.urlopen(req, timeout=self._timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return VLMOutput(caption=str(body.get("response", "")).strip())
