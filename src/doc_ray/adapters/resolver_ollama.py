"""Frontier entity resolver over a local Ollama server (alternative to Anthropic).

⚠ ARCHITECTURAL CONTRACT (PALS's LAW) — LLM OUTPUT IS UNVERIFIED BY DEFAULT.
A local model is *more* error-prone than a frontier one, not less, so this adapter
applies the exact same defence as the Claude backend: the candidate-constrained JSON
schema is sent as Ollama's ``format`` (best-effort constrained decoding), and every
returned verdict is re-validated by
:func:`~doc_ray.adapters.resolver_shared.to_resolution` — a hallucinated or out-of-set
id is coerced to ABSTAIN and routed to human review, never committed.

Transport/HTTP errors propagate (the Postgres job queue retries with backoff, matching
the Claude backend); only a well-formed-but-invalid verdict abstains. Runs worker-only,
on the residue the remissive index could not resolve.
"""

from __future__ import annotations

import json
import os

import httpx

from doc_ray.adapters.resolver_shared import (
    SYSTEM_PROMPT,
    CandidateResolutionVerifier,
    build_schema,
    render_prompt,
    to_resolution,
)
from doc_ray.application.verification import ModelOutputVerificationError, verify_model_output
from doc_ray.ports.resolver import Candidate, Resolution, ResolutionKind

#: Default local model — override with DOCRAY_OLLAMA_MODEL. Any tool/JSON-capable
#: model works; the verification layer catches whatever the schema does not constrain.
DEFAULT_OLLAMA_MODEL = "qwen2.5"
#: Ollama server base URL — override with DOCRAY_OLLAMA_HOST (``http://ollama:11434``
#: inside Compose, ``http://localhost:11434`` on the host).
DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_TIMEOUT_SECONDS = 120.0


def _abstain() -> Resolution:
    """The fail-safe verdict for any unusable response."""
    return Resolution(kind=ResolutionKind.ABSTAIN, confidence=0.0)


class OllamaResolver:
    """:class:`~doc_ray.ports.resolver.EntityResolver` over Ollama's ``/api/chat``.

    Attributes:
        name: Tool identity recorded on review rows (``ollama/<model>``).
    """

    def __init__(
        self,
        model: str | None = None,
        *,
        host: str | None = None,
        timeout_seconds: float | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        """Configure the resolver against a local Ollama server.

        Args:
            model: Model id; defaults to ``DOCRAY_OLLAMA_MODEL`` or the pinned default.
            host: Ollama base URL; defaults to ``DOCRAY_OLLAMA_HOST`` or localhost.
            timeout_seconds: Request timeout; defaults to ``DOCRAY_RESOLVER_TIMEOUT_SECONDS``.
            client: Injected HTTP client (tests); a real ``httpx.Client`` otherwise.
        """
        self._model = model or os.environ.get("DOCRAY_OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
        base = host or os.environ.get("DOCRAY_OLLAMA_HOST", DEFAULT_OLLAMA_HOST)
        self._host = base.rstrip("/")
        timeout = timeout_seconds
        if timeout is None:
            timeout = float(
                os.environ.get("DOCRAY_RESOLVER_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
            )
        self._client = client if client is not None else httpx.Client(timeout=timeout)
        self.name = f"ollama/{self._model}"

    def resolve(self, mention: str, sentence: str, candidates: list[Candidate]) -> Resolution:
        """Return Ollama's constrained verdict for one mention (validated).

        Args:
            mention: The mention surface text.
            sentence: The sentence providing disambiguating context.
            candidates: The Stage-2 candidate set the verdict must choose from.

        Returns:
            A validated :class:`Resolution`; any unusable response resolves to ABSTAIN.
        """
        response = self._client.post(
            f"{self._host}/api/chat",
            json={
                "model": self._model,
                "stream": False,
                "format": build_schema(candidates),
                "options": {"temperature": 0},
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": render_prompt(mention, sentence, candidates)},
                ],
            },
        )
        response.raise_for_status()  # transport/HTTP errors propagate -> job retry
        raw = _parse_chat_content(response.json())
        if raw is None:
            return _abstain()
        try:
            verified = verify_model_output(raw, CandidateResolutionVerifier(candidates))
        except ModelOutputVerificationError:
            return _abstain()
        return to_resolution(dict(verified), candidates)


def _parse_chat_content(data: object) -> dict[str, object] | None:
    """Extract and JSON-decode the assistant message body (None if unusable)."""
    message = data.get("message") if isinstance(data, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        parsed = json.loads(content)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None
