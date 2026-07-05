"""Shared verification-stage tests for untrusted model output."""

from __future__ import annotations

import pytest

from doc_ray.application.verification import ModelOutputVerificationError, verify_model_output
from doc_ray.ports.verification import VerificationIssue, VerificationResult


class _PassVerifier:
    name = "pass"

    def verify(self, output, *, context=None):
        return VerificationResult.passed(self.name)


class _FailVerifier:
    name = "fail"

    def verify(self, output, *, context=None):
        return VerificationResult.failed(
            self.name,
            [VerificationIssue("bad-output", "model output did not match the contract")],
        )


def test_verify_model_output_returns_output_when_verifier_accepts():
    output = {"verdict": "abstain"}

    assert verify_model_output(output, _PassVerifier()) is output


def test_verify_model_output_rejects_failed_verification():
    with pytest.raises(ModelOutputVerificationError) as exc:
        verify_model_output({"verdict": "select"}, _FailVerifier())

    assert exc.value.result.verifier == "fail"
    assert exc.value.result.issues[0].code == "bad-output"
