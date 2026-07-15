"""Backend-agnostic frontier-resolver logic shared by every EntityResolver adapter.

⚠ ARCHITECTURAL CONTRACT (PALS's LAW) — LLM OUTPUT IS UNVERIFIED BY DEFAULT.
The constrained-choice schema, prompt, verifier, and ``to_resolution`` mapping live
here so every backend (Anthropic, Ollama, …) enforces the same guarantee: a verdict
outside the offered candidate set — a hallucinated id — is coerced to ABSTAIN and
routed to human review, never committed. Swapping the model must never weaken that.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from doc_ray.ports.resolver import Candidate, Resolution, ResolutionKind
from doc_ray.ports.verification import VerificationIssue, VerificationResult

#: Tool / JSON-schema name the constrained choice is delivered under.
TOOL_NAME = "resolve_entity"

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

#: System prompt shared by every backend — the rules are model-independent.
SYSTEM_PROMPT = (
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


def build_schema(candidates: list[Candidate]) -> dict[str, Any]:
    """Build the strict JSON schema whose ``canonical_id`` enum *is* the candidate set.

    With no candidates, ``canonical_id`` only admits ``null`` (an empty JSON-Schema
    enum is invalid), so the model can only declare a new entity or abstain. This is
    the raw schema both an Anthropic strict tool and an Ollama ``format`` reuse.

    Args:
        candidates: The Stage-2 candidate set for the mention.

    Returns:
        A JSON schema object constraining the verdict to the offered ids.
    """
    ids = [c.canonical_id for c in candidates]
    id_schema: dict[str, Any]
    if ids:
        id_schema = {"anyOf": [{"type": "integer", "enum": ids}, {"type": "null"}]}
    else:
        id_schema = {"type": "null"}
    return {
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
    }


def build_tool(candidates: list[Candidate]) -> dict[str, Any]:
    """Wrap :func:`build_schema` in a strict Anthropic tool definition.

    Args:
        candidates: The Stage-2 candidate set for the mention.

    Returns:
        A strict Anthropic tool definition for the constrained choice.
    """
    return {
        "name": TOOL_NAME,
        "description": (
            "Resolve the mention to one candidate canonical entity, declare a new "
            "entity, or abstain. canonical_id must be null unless verdict is 'select'."
        ),
        "strict": True,
        "input_schema": build_schema(candidates),
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


def render_prompt(mention: str, sentence: str, candidates: list[Candidate]) -> str:
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
