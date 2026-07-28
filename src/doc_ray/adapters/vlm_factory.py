"""Select the Tier-3 caption VLM backend from ``DOCRAY_VLM_BACKEND``.

``ollama`` (default) drives a local Ollama server; ``vllm`` drives any
OpenAI-compatible ``/chat/completions`` endpoint — a local vLLM or a hosted
serverless one (e.g. DigitalOcean inference), authenticated via
``DOCRAY_VLM_API_KEY``. Both implement the same
:class:`~doc_ray.ports.vlm.VisionLanguageModel` port and emit the same
feature-report captions, so the choice is a deployment/cost decision, not a
behavioural one — mirroring :mod:`doc_ray.adapters.resolver_factory`. Adapter
modules are imported lazily so selecting one backend never imports the other.
"""

from __future__ import annotations

import os

from doc_ray.ports.vlm import VisionLanguageModel

#: Env var selecting the backend; unset/empty defaults to ``ollama``.
BACKEND_ENV = "DOCRAY_VLM_BACKEND"
DEFAULT_BACKEND = "ollama"


def make_vlm(backend: str | None = None) -> VisionLanguageModel:
    """Construct the configured :class:`VisionLanguageModel`.

    Args:
        backend: Explicit backend name (overrides the env var); ``None`` reads
            ``DOCRAY_VLM_BACKEND`` and falls back to ``ollama``. An empty
            string (compose passing an unset variable through) also falls back.

    Returns:
        A ready :class:`VisionLanguageModel` for the selected backend.

    Raises:
        ValueError: If the backend name is neither ``ollama`` nor ``vllm``
            (fail closed rather than silently defaulting a typo).
    """
    name = (backend or os.environ.get(BACKEND_ENV) or DEFAULT_BACKEND).strip().lower()
    if name == "ollama":
        from doc_ray.adapters.vlm_ollama import OllamaVisionLanguageModel

        return OllamaVisionLanguageModel()
    if name == "vllm":
        from doc_ray.adapters.vlm_vllm import VLLMVisionLanguageModel

        return VLLMVisionLanguageModel()
    raise ValueError(f"unknown {BACKEND_ENV} {name!r}; expected 'ollama' or 'vllm'")
