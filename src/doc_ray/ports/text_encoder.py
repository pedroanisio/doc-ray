"""Port: a learned sentence encoder for semantic retrieval (NLP Layer 20).

Replaces the discarded hash-BoW placeholder. A sentence-transformers-class model
embeds text into a single learned vector space, so semantically similar
sentences are close under cosine distance (unlike the hash-BoW vector, where
distinct words hashed to unrelated buckets). Concrete adapters
(sentence-transformers, served on the worker) implement this port; ``embed_texts``
returns L2-normalizable float vectors of length ``dim``.

PALS posture: encoder output is used ONLY for ranking; it never asserts a fact
into the trusted graph (mirrors ``ports/vision_encoder.py``). A temperature-free
forward pass is reproducible given fixed weights and preprocessing, so it needs
no verification gate — it is safe by *use*, not by verification.

The port keeps the application/handler code independent of the serving stack and
testable with a deterministic fake.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class TextEncoder(Protocol):
    """Embed text into one learned vector space for semantic retrieval."""

    #: Stable model identifier recorded as the ``model`` provenance on each row.
    model_id: str
    #: Output vector dimension (must match the ``embedding.vector(D)`` column).
    dim: int

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, in the shared learned space."""
        ...
