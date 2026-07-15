"""Frontier entity resolver over the Anthropic API (Stage 3, D-018).

⚠ ARCHITECTURAL CONTRACT (PALS's LAW) — LLM OUTPUT IS UNVERIFIED BY DEFAULT.
LLMs statistically produce errors: omissions, hallucinations, partial completions,
schema violations, and silent failures. This adapter therefore never trusts the
model. The constrained choice is enforced twice:

1. **At the schema** — the model is forced (``tool_choice``) into a strict tool
   (:func:`~doc_ray.adapters.resolver_shared.build_tool`) whose ``canonical_id`` enum
   *is* the Stage-2 candidate set, so a freehand id is rejected by the API's own
   validation.
2. **At the adapter** — :func:`~doc_ray.adapters.resolver_shared.to_resolution`
   re-validates everything the model returns; a selection outside the candidate set,
   a malformed verdict, a NEW without a name, or a missing tool call is **coerced to
   abstain**, which routes to human review instead of becoming a confident wrong link.

Model calls run in the synchronous worker only (D-005), never on the API event
loop, and only on the residue the remissive index could not resolve (D-019) — so
the expensive call happens once per novel form. ``temperature`` is not sent: it is
rejected on current Claude models; determinism comes from the strict schema and
the constrained choice, not sampling parameters.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, cast

from doc_ray.adapters.resolver_shared import (
    SYSTEM_PROMPT,
    TOOL_NAME,
    CandidateResolutionVerifier,
    build_tool,
    render_prompt,
    to_resolution,
)
from doc_ray.application.verification import ModelOutputVerificationError, verify_model_output
from doc_ray.ports.resolver import Candidate, Resolution, ResolutionKind

if TYPE_CHECKING:
    # Typing only — the SDK itself is imported lazily in ClaudeResolver.__init__ so
    # the package stays importable without the anthropic dependency installed.
    from anthropic.types import MessageParam, ToolChoiceToolParam, ToolParam

#: Default frontier model (override with DOCRAY_RESOLVER_MODEL).
DEFAULT_MODEL = "claude-opus-4-8"
DEFAULT_TIMEOUT_SECONDS = 60.0


class ClaudeResolver:
    """:class:`~doc_ray.ports.resolver.EntityResolver` over the Anthropic API.

    The SDK is imported lazily so the package stays importable (and the stub-based
    tests runnable) without the ``anthropic`` dependency or an API key present.

    Attributes:
        name: Tool identity recorded on review rows (``claude/<model>``).
    """

    def __init__(
        self,
        model: str | None = None,
        *,
        max_tokens: int = 1024,
        timeout_seconds: float | None = None,
    ) -> None:
        """Open the Anthropic client.

        Args:
            model: Model id; defaults to ``DOCRAY_RESOLVER_MODEL`` or the pinned default.
            max_tokens: Response cap — the verdict is one small tool call.
            timeout_seconds: Request timeout; defaults to ``DOCRAY_RESOLVER_TIMEOUT_SECONDS``.
        """
        import anthropic  # lazy: worker-only dependency

        timeout = timeout_seconds
        if timeout is None:
            timeout = float(
                os.environ.get("DOCRAY_RESOLVER_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
            )
        self._client = anthropic.Anthropic(timeout=timeout)
        self._model = model or os.environ.get("DOCRAY_RESOLVER_MODEL", DEFAULT_MODEL)
        self._max_tokens = max_tokens
        self.name = f"claude/{self._model}"

    def resolve(self, mention: str, sentence: str, candidates: list[Candidate]) -> Resolution:
        """Return the model's constrained verdict for one mention (validated).

        Args:
            mention: The mention surface text.
            sentence: The sentence providing disambiguating context.
            candidates: The Stage-2 candidate set the verdict must choose from.

        Returns:
            A validated :class:`Resolution`; any unexpected response shape
            (including a refusal or a missing tool call) resolves to ABSTAIN.
        """
        prompt = render_prompt(mention, sentence, candidates)
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=SYSTEM_PROMPT,
            tools=[cast("ToolParam", build_tool(candidates))],
            tool_choice=cast("ToolChoiceToolParam", {"type": "tool", "name": TOOL_NAME}),
            messages=[cast("MessageParam", {"role": "user", "content": prompt})],
        )
        block = next((b for b in response.content if b.type == "tool_use"), None)
        raw = block.input if block is not None and isinstance(block.input, dict) else {}
        try:
            verified = verify_model_output(raw, CandidateResolutionVerifier(candidates))
        except ModelOutputVerificationError:
            return Resolution(kind=ResolutionKind.ABSTAIN, confidence=0.0)
        return to_resolution(dict(verified), candidates)
