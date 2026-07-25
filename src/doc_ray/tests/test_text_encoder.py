"""Contract tests for the ``TextEncoder`` port (``ports/text_encoder.py``).

The port is the seam that keeps the semantic-embedding writer/reader independent
of the sentence-transformers serving stack (absent from the API image, like the
vision stack) and testable with a deterministic fake. These tests pin the
structural contract: a conforming fake is an instance; anything missing
``embed_texts`` is not.
"""

from __future__ import annotations

import hashlib

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
