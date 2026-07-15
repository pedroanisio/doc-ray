"""Select the frontier-resolver backend from ``DOCRAY_RESOLVER_BACKEND``.

``anthropic`` (default) uses the Claude API; ``ollama`` uses a local Ollama server.
Both implement the same :class:`~doc_ray.ports.resolver.EntityResolver` contract and
the same PALS verification, so the choice is a deployment/cost decision, not a
behavioural one. Adapter modules are imported lazily so selecting one backend never
pulls in the other's dependency (the ``anthropic`` SDK, or ``httpx`` transport setup).
"""

from __future__ import annotations

import os

from doc_ray.ports.resolver import EntityResolver

#: Env var selecting the backend; unset defaults to ``anthropic``.
BACKEND_ENV = "DOCRAY_RESOLVER_BACKEND"
DEFAULT_BACKEND = "anthropic"


def make_resolver(backend: str | None = None) -> EntityResolver:
    """Construct the configured :class:`EntityResolver`.

    Args:
        backend: Explicit backend name (overrides the env var); ``None`` reads
            ``DOCRAY_RESOLVER_BACKEND`` and falls back to ``anthropic``.

    Returns:
        A ready :class:`EntityResolver` for the selected backend.

    Raises:
        ValueError: If the backend name is neither ``anthropic`` nor ``ollama``
            (fail closed rather than silently defaulting a typo).
    """
    name = (backend or os.environ.get(BACKEND_ENV, DEFAULT_BACKEND)).strip().lower()
    if name == "anthropic":
        from doc_ray.adapters.resolver_claude import ClaudeResolver

        return ClaudeResolver()
    if name == "ollama":
        from doc_ray.adapters.resolver_ollama import OllamaResolver

        return OllamaResolver()
    raise ValueError(f"unknown {BACKEND_ENV} {name!r}; expected 'anthropic' or 'ollama'")
