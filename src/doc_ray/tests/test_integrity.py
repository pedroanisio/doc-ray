from __future__ import annotations

import hashlib

import pytest

from doc_ray.application.integrity import IntegrityError, sha256_hex, verify_bytes


def test_verify_bytes_accepts_matching_sha256() -> None:
    data = b"trusted bytes"

    assert verify_bytes(data, hashlib.sha256(data).hexdigest(), context="unit") == data


def test_verify_bytes_rejects_mismatched_sha256() -> None:
    with pytest.raises(IntegrityError, match="sha256 mismatch"):
        verify_bytes(b"corrupt", "0" * 64, context="unit")


def test_sha256_hex_matches_hashlib() -> None:
    assert sha256_hex(b"abc") == hashlib.sha256(b"abc").hexdigest()
