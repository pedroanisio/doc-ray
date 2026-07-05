"""Application-stage gate for untrusted model output."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from doc_ray.ports.verification import ModelOutputVerifier, VerificationResult


class ModelOutputVerificationError(ValueError):
    """Raised when untrusted model output fails its verifier contract."""

    def __init__(self, result: VerificationResult) -> None:
        """Capture the failed verification result for audit/logging."""
        message = "; ".join(issue.code for issue in result.issues) or "verification failed"
        super().__init__(f"{result.verifier}: {message}")
        self.result = result


def verify_model_output(
    output: Mapping[str, Any],
    verifier: ModelOutputVerifier,
    *,
    context: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    """Return ``output`` only after ``verifier`` accepts it.

    Args:
        output: Untrusted model output.
        verifier: Contract-specific verifier.
        context: Optional immutable context, such as the offered candidate set.

    Raises:
        ModelOutputVerificationError: If the verifier rejects ``output``.

    Returns:
        The original output mapping when verification succeeds.
    """
    result = verifier.verify(output, context=context)
    if not result.ok:
        raise ModelOutputVerificationError(result)
    return output
