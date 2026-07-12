"""Read-side byte integrity checks for object-store content."""

from __future__ import annotations

import hashlib


class IntegrityError(ValueError):
    """Raised when fetched bytes do not match their recorded digest."""


def sha256_hex(data: bytes) -> str:
    """Return the hex SHA-256 digest of *data*."""
    return hashlib.sha256(data).hexdigest()


def verify_bytes(data: bytes, expected_sha256: str | None, *, context: str) -> bytes:
    """Return *data* after verifying its SHA-256 digest when one is known."""
    if not expected_sha256:
        return data
    actual = sha256_hex(data)
    if actual != expected_sha256:
        raise IntegrityError(
            f"sha256 mismatch for {context}: expected {expected_sha256}, got {actual}"
        )
    return data
