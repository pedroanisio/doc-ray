"""Spec for how a failed job's exception decides its retry delay.

The motivating failure: a GPU annotate lane saturated, and dozens of
documents failed with ``CUDA out of memory`` within the same hour. The retry
schedule at the time was 5s then 10s, so all three attempts landed inside 15
seconds of the first — every one of them into the same saturated GPU. The jobs
went terminal, and because ``annotate`` is the head of the pipeline chain, those
documents were left with no downstream jobs at all.

The failure was transient. The schedule was not built to notice that.
"""

from __future__ import annotations

import urllib.error

from doc_ray.domain.retry_policy import (
    CONTENTION_BASE_SECONDS,
    DETERMINISTIC_BASE_SECONDS,
    FailureClass,
    backoff_seconds,
    classify,
)


class _CudaOutOfMemoryError(RuntimeError):
    """Stands in for ``torch.cuda.OutOfMemoryError`` without importing torch."""

    __name__ = "OutOfMemoryError"


def test_cuda_exhaustion_is_contention() -> None:
    exc = _CudaOutOfMemoryError(
        "CUDA out of memory. Tried to allocate 162.00 MiB. GPU 0 has a total "
        "capacity of 79.18 GiB of which 29.88 MiB is free."
    )
    assert classify(exc) is FailureClass.CONTENTION


def test_host_memory_exhaustion_is_contention() -> None:
    assert classify(MemoryError()) is FailureClass.CONTENTION


def test_refused_connection_to_a_model_endpoint_is_contention() -> None:
    """A whole caption wave lost to a refusing endpoint is this class."""
    exc = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
    assert classify(exc) is FailureClass.CONTENTION


def test_rate_limiting_and_unavailability_are_contention() -> None:
    for status in (429, 502, 503, 504):
        exc = urllib.error.HTTPError("http://x", status, "busy", {}, None)  # type: ignore[arg-type]
        assert classify(exc) is FailureClass.CONTENTION, status


def test_a_served_error_is_not_contention() -> None:
    """A 400 will fail identically on retry; only waiting longer wastes the lane."""
    exc = urllib.error.HTTPError("http://x", 400, "bad request", {}, None)  # type: ignore[arg-type]
    assert classify(exc) is FailureClass.DETERMINISTIC


def test_a_programming_error_is_deterministic() -> None:
    """`caption_document_images() got an unexpected keyword argument 'resume'`
    failed 163 times; no delay would ever have helped."""
    assert classify(TypeError("got an unexpected keyword argument 'resume'")) is (
        FailureClass.DETERMINISTIC
    )


def test_a_missing_relation_is_deterministic() -> None:
    assert classify(ValueError('relation "entity_mention_correction" does not exist')) is (
        FailureClass.DETERMINISTIC
    )


def test_contention_waits_far_longer_than_a_deterministic_failure() -> None:
    """The whole point: a busy GPU must not be re-hit seconds later."""
    assert backoff_seconds(1, FailureClass.CONTENTION) > backoff_seconds(
        1, FailureClass.DETERMINISTIC
    )
    assert backoff_seconds(1, FailureClass.CONTENTION) == CONTENTION_BASE_SECONDS
    assert backoff_seconds(1, FailureClass.DETERMINISTIC) == DETERMINISTIC_BASE_SECONDS


def test_the_incident_window_is_survivable() -> None:
    """Three attempts must span the burst that killed 55 documents (~44 minutes)."""
    total = sum(backoff_seconds(n, FailureClass.CONTENTION) for n in (1, 2))
    assert total >= 44 * 60, f"three attempts only span {total / 60:.0f} minutes"


def test_backoff_still_grows_and_stays_capped() -> None:
    for failure_class in FailureClass:
        delays = [backoff_seconds(n, failure_class) for n in range(1, 12)]
        assert delays == sorted(delays), failure_class
        assert delays[-1] <= 24 * 3600, failure_class


def test_first_attempt_never_returns_a_negative_or_zero_delay() -> None:
    for failure_class in FailureClass:
        assert backoff_seconds(0, failure_class) > 0
