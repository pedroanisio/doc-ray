"""Port: verify untrusted model output before application code may consume it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class VerificationIssue:
    """One model-output contract violation."""

    code: str
    message: str
    path: str = ""


@dataclass(frozen=True)
class VerificationResult:
    """Verifier verdict for one untrusted model-output payload."""

    verifier: str
    ok: bool
    issues: tuple[VerificationIssue, ...] = field(default_factory=tuple)

    @classmethod
    def passed(cls, verifier: str) -> VerificationResult:
        """Build a successful verification result."""
        return cls(verifier=verifier, ok=True)

    @classmethod
    def failed(
        cls, verifier: str, issues: list[VerificationIssue] | tuple[VerificationIssue, ...]
    ) -> VerificationResult:
        """Build a failed verification result."""
        return cls(verifier=verifier, ok=False, issues=tuple(issues))


class ModelOutputVerifier(Protocol):
    """Verify one untrusted model-output payload against a concrete contract."""

    name: str

    def verify(
        self, output: Mapping[str, Any], *, context: Mapping[str, Any] | None = None
    ) -> VerificationResult:
        """Return whether ``output`` satisfies this verifier's contract."""
        ...
