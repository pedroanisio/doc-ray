"""Contract tests for the ``TextEncoder`` port (``ports/text_encoder.py``).

The port is the seam that keeps the semantic-embedding writer/reader independent
of the sentence-transformers serving stack (absent from the API image, like the
vision stack) and testable with a deterministic fake. These tests pin the
structural contract: a conforming fake is an instance; anything missing
``embed_texts`` is not.
"""

from __future__ import annotations

import hashlib

import pytest

from doc_ray.adapters.text_encoder_sentence_transformers import resolve_device
from doc_ray.ports.text_encoder import TextEncoder


class _FakeTextEncoder:
    """Deterministic content-hash encoder — no model, no dependency."""

    model_id = "fake-sbert-8"
    dim = 8

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            digest = hashlib.blake2b(text.encode("utf-8"), digest_size=self.dim).digest()
            vectors.append([b / 255.0 for b in digest])
        return vectors


def test_fake_encoder_conforms_to_protocol() -> None:
    assert isinstance(_FakeTextEncoder(), TextEncoder)


def test_object_missing_embed_texts_does_not_conform() -> None:
    class NotAnEncoder:
        model_id = "x"
        dim = 8

    assert not isinstance(NotAnEncoder(), TextEncoder)


def test_embed_texts_is_deterministic_and_correct_dimension() -> None:
    encoder = _FakeTextEncoder()
    first = encoder.embed_texts(["hello world", "a second sentence"])
    second = encoder.embed_texts(["hello world", "a second sentence"])
    assert first == second  # reproducible: same input → same vectors
    assert all(len(vec) == encoder.dim for vec in first)
    assert len(first) == 2  # one vector per input text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),  # unset → let sentence-transformers auto-select (CUDA if present)
        ("", None),
        ("auto", None),
        ("AUTO", None),
        ("  auto  ", None),
        ("cpu", "cpu"),  # explicit override wins over auto-detection
        ("cuda", "cuda"),
        ("CUDA", "cuda"),
        ("cuda:1", "cuda:1"),  # device index preserved
        ("mps", "mps"),
    ],
)
def test_resolve_device_maps_env_to_sentence_transformers_arg(
    raw: str | None, expected: str | None
) -> None:
    """DOCRAY_SEMANTIC_DEVICE=auto/unset → None (auto); anything else → that device.

    This is the app-layer half of the environment assessment: the deploy layer
    grants (or withholds) the GPU to the container; this lets an operator force a
    device explicitly — e.g. CPU when an 8 GB card is already held by trf
    annotation — without touching code.
    """
    assert resolve_device(raw) == expected
