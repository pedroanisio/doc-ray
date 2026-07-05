"""Frontier entity resolver over the Anthropic API (Stage 3, D-018).

⚠ ARCHITECTURAL CONTRACT (PALS's LAW) — LLM OUTPUT IS UNVERIFIED BY DEFAULT.
LLMs statistically produce errors: omissions, hallucinations, partial completions,
schema violations, and silent failures. This adapter therefore never trusts the
model. The constrained choice is enforced twice:

1. **At the schema** — the model is forced (``tool_choice``) into a strict tool
   whose ``canonical_id`` enum *is* the Stage-2 candidate set, so a freehand id is
   rejected by the API's own validation.
2. **At the adapter** — :func:`to_resolution` re-validates everything the model
   returns; a selection outside the candidate set, a malformed verdict, a NEW
   without a name, or a missing tool call is **coerced to abstain**, which routes
   to human review instead of becoming a confident wrong link.

Model calls run in the synchronous worker only (D-005), never on the API event
loop, and only on the residue the remissive index could not resolve (D-019) — so
the expensive call happens once per novel form. ``temperature`` is not sent: it is
rejected on current Claude models; determinism comes from the strict schema and
the constrained choice, not sampling parameters.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from doc_ray.application.verification import ModelOutputVerificationError, verify_model_output
from doc_ray.ports.resolver import Candidate, Resolution, ResolutionKind
from doc_ray.ports.verification import VerificationIssue, VerificationResult

if TYPE_CHECKING:
    # Typing only — the SDK itself is imported lazily in ClaudeResolver.__init__ so
    # the package stays importable without the anthropic dependency installed.
    from anthropic.types import MessageParam, ToolChoiceToolParam, ToolParam

#: Default frontier model (override with DOCRAY_RESOLVER_MODEL).
DEFAULT_MODEL = "claude-opus-4-8"

_TOOL_NAME = "resolve_entity"

#: Closed entity-class vocabulary the resolver may propose for a NEW entity
#: (constrained choice — free-text types would reintroduce unvalidated output).
ENTITY_TYPES = (
    "PERSON",
    "PLACE",
    "ORG",
    "EVENT",
    "WORK",
    "ANIMAL",
    "CONCEPT",
    "OTHER",
)

_SYSTEM = (
    "You are an entity-resolution engine. You are given a mention in its sentence "
    "and a closed set of candidate canonical entities. You must call the "
    "resolve_entity tool exactly once. Rules: pick verdict='select' with the "
    "canonical_id of a candidate ONLY when the sentence context supports it; use "
    "verdict='new_entity' with a preferred name when the mention is a real, "
    "distinct entity not in the candidates; use verdict='abstain' whenever you are "
    "unsure. Never invent identifiers. Report harvested alternate surface forms "
    "you observed for the entity (aliases), and a confidence in [0, 1]."
)


class CandidateResolutionVerifier:
    """Verify one raw resolver verdict against the offered candidate set."""

    name = "candidate-resolution.v1"

    def __init__(self, candidates: list[Candidate]) -> None:
        """Store the candidate set that constrains any SELECT verdict."""
        self._candidate_ids = {c.canonical_id for c in candidates}

    def verify(
        self, output: Mapping[str, Any], *, context: Mapping[str, Any] | None = None
    ) -> VerificationResult:
        """Reject malformed verdicts before application code consumes them."""
        issues: list[VerificationIssue] = []
        verdict = output.get("verdict")
        if verdict not in {k.value for k in ResolutionKind}:
            issues.append(VerificationIssue("unknown-verdict", "verdict is not allowed", "verdict"))
        confidence = output.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            issues.append(
                VerificationIssue("invalid-confidence", "confidence must be numeric", "confidence")
            )
        aliases = output.get("aliases", [])
        if aliases is not None and not isinstance(aliases, list):
            issues.append(VerificationIssue("invalid-aliases", "aliases must be a list", "aliases"))
        if verdict == ResolutionKind.SELECT.value:
            cid = output.get("canonical_id")
            if isinstance(cid, bool) or not isinstance(cid, int):
                issues.append(
                    VerificationIssue("invalid-canonical-id", "canonical_id must be an int")
                )
            elif cid not in self._candidate_ids:
                issues.append(
                    VerificationIssue(
                        "canonical-id-not-offered",
                        "canonical_id was not in the offered candidate set",
                        "canonical_id",
                    )
                )
        if verdict == ResolutionKind.NEW.value:
            preferred = output.get("new_preferred")
            if not isinstance(preferred, str) or not preferred.strip():
                issues.append(
                    VerificationIssue(
                        "missing-new-preferred",
                        "new_entity verdict requires a preferred name",
                        "new_preferred",
                    )
                )
        if issues:
            return VerificationResult.failed(self.name, issues)
        return VerificationResult.passed(self.name)


def build_tool(candidates: list[Candidate]) -> dict[str, Any]:
    """Build the strict tool schema whose id enum *is* the candidate set.

    With no candidates, ``canonical_id`` only admits ``null`` (an empty JSON-Schema
    enum is invalid), so the model can only declare a new entity or abstain.

    Args:
        candidates: The Stage-2 candidate set for the mention.

    Returns:
        A strict Anthropic tool definition for the constrained choice.
    """
    ids = [c.canonical_id for c in candidates]
    id_schema: dict[str, Any]
    if ids:
        id_schema = {"anyOf": [{"type": "integer", "enum": ids}, {"type": "null"}]}
    else:
        id_schema = {"type": "null"}
    return {
        "name": _TOOL_NAME,
        "description": (
            "Resolve the mention to one candidate canonical entity, declare a new "
            "entity, or abstain. canonical_id must be null unless verdict is 'select'."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["select", "new_entity", "abstain"],
                    "description": "The constrained choice.",
                },
                "canonical_id": {
                    **id_schema,
                    "description": "Chosen candidate id (select only), else null.",
                },
                "confidence": {
                    "type": "number",
                    "description": "Confidence in [0, 1].",
                },
                "new_preferred": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "description": "Preferred label (new_entity only), else null.",
                },
                "new_entity_type": {
                    "anyOf": [{"type": "string", "enum": list(ENTITY_TYPES)}, {"type": "null"}],
                    "description": "Entity class (new_entity only), else null.",
                },
                "aliases": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Alternate surface forms observed for the entity.",
                },
            },
            "required": [
                "verdict",
                "canonical_id",
                "confidence",
                "new_preferred",
                "new_entity_type",
                "aliases",
            ],
            "additionalProperties": False,
        },
    }


def _clamped_confidence(raw: object) -> float:
    """Coerce a reported confidence to a float in [0, 1] (junk -> 0.0).

    NaN is handled explicitly: ``min(1.0, nan)`` returns 1.0 in Python (NaN
    comparisons are order-dependent), so without this guard a NaN confidence
    would clamp to full confidence and auto-commit — the opposite of PALS.
    """
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return 0.0
    value = float(raw)
    if math.isnan(value) or math.isinf(value):
        return 0.0
    return max(0.0, min(1.0, value))


def _clean_aliases(raw: object) -> tuple[str, ...]:
    """Keep only non-empty string aliases from the model output."""
    if not isinstance(raw, list):
        return ()
    return tuple(a.strip() for a in raw if isinstance(a, str) and a.strip())


def to_resolution(raw: dict[str, Any], candidates: list[Candidate]) -> Resolution:
    """Validate raw tool input into a :class:`Resolution` (PALS enforcement).

    Anything that does not validate — an unknown verdict, a selection outside the
    candidate set (a hallucinated id), a boolean masquerading as an id, or a new
    entity without a name — is coerced to ABSTAIN so it routes to review rather
    than being committed.

    Args:
        raw: The tool_use input as returned by the model (untrusted).
        candidates: The candidate set that was offered.

    Returns:
        A validated :class:`Resolution`; SELECT ids are guaranteed to be members
        of *candidates*.
    """
    confidence = _clamped_confidence(raw.get("confidence"))
    aliases = _clean_aliases(raw.get("aliases"))
    abstain = Resolution(
        kind=ResolutionKind.ABSTAIN, confidence=confidence, harvested_aliases=aliases
    )

    verdict = raw.get("verdict")
    if verdict == ResolutionKind.SELECT.value:
        cid = raw.get("canonical_id")
        allowed = {c.canonical_id for c in candidates}
        if isinstance(cid, bool) or not isinstance(cid, int) or cid not in allowed:
            return abstain  # out-of-set / malformed id -> never committed
        return Resolution(
            kind=ResolutionKind.SELECT,
            canonical_id=cid,
            confidence=confidence,
            harvested_aliases=aliases,
        )
    if verdict == ResolutionKind.NEW.value:
        preferred = raw.get("new_preferred")
        if not isinstance(preferred, str) or not preferred.strip():
            return abstain  # a new entity without a name is not actionable
        proposed_type = raw.get("new_entity_type")
        entity_type = (
            proposed_type.strip().upper()
            if isinstance(proposed_type, str) and proposed_type.strip()
            else None
        )  # junk type drops to None (mention type wins); the verdict itself survives
        return Resolution(
            kind=ResolutionKind.NEW,
            confidence=confidence,
            harvested_aliases=aliases,
            new_preferred=preferred.strip(),
            new_entity_type=entity_type,
        )
    return abstain  # unknown verdict (or explicit abstain)


def _render_prompt(mention: str, sentence: str, candidates: list[Candidate]) -> str:
    """Render the user prompt: the mention in context plus the candidate set."""
    lines = [
        f"Mention: {mention!r}",
        f"Sentence: {sentence!r}" if sentence else "Sentence: (not available)",
        "Candidates:",
    ]
    if candidates:
        for c in candidates:
            desc = f" — {c.description}" if c.description else ""
            lines.append(f"  id={c.canonical_id}: {c.preferred} [{c.entity_type}]{desc}")
    else:
        lines.append("  (none)")
    return "\n".join(lines)


class ClaudeResolver:
    """:class:`~doc_ray.ports.resolver.EntityResolver` over the Anthropic API.

    The SDK is imported lazily so the package stays importable (and the stub-based
    tests runnable) without the ``anthropic`` dependency or an API key present.

    Attributes:
        name: Tool identity recorded on review rows (``claude/<model>``).
    """

    def __init__(self, model: str | None = None, *, max_tokens: int = 1024) -> None:
        """Open the Anthropic client.

        Args:
            model: Model id; defaults to ``DOCRAY_RESOLVER_MODEL`` or the pinned default.
            max_tokens: Response cap — the verdict is one small tool call.
        """
        import anthropic  # lazy: worker-only dependency

        self._client = anthropic.Anthropic()
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
        prompt = _render_prompt(mention, sentence, candidates)
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=_SYSTEM,
            tools=[cast("ToolParam", build_tool(candidates))],
            tool_choice=cast("ToolChoiceToolParam", {"type": "tool", "name": _TOOL_NAME}),
            messages=[cast("MessageParam", {"role": "user", "content": prompt})],
        )
        block = next((b for b in response.content if b.type == "tool_use"), None)
        raw = block.input if block is not None and isinstance(block.input, dict) else {}
        try:
            verified = verify_model_output(raw, CandidateResolutionVerifier(candidates))
        except ModelOutputVerificationError:
            return Resolution(kind=ResolutionKind.ABSTAIN, confidence=0.0)
        return to_resolution(dict(verified), candidates)
