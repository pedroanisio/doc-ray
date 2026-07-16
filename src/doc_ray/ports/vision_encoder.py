"""Port: a dual-encoder vision model for cross-modal retrieval (Prism Tier-2).

A CLIP/SigLIP-class model that embeds images AND text into the *same* vector
space, so text->image search works. Both towers must come from one model — the
existing hash-BoW sentence ``embedding`` is non-semantic and lives in a different
space, so it cannot be used for the text tower (proposal §3, Tier-2).

Concrete adapters (open_clip / transformers / sentence-transformers, served on
the GPU) implement this port; ``embed_*`` returns L2-normalizable float vectors
of length ``dim``. The port keeps the application/handler code independent of the
serving stack and testable with a deterministic fake.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class VisionEncoder(Protocol):
    """Embed images and text into one shared vector space."""

    #: Stable model identifier recorded as the ``model`` provenance on each row.
    model_id: str
    #: Output vector dimension (must match the ``vector(D)`` migration columns).
    dim: int

    def embed_images(self, images: list[bytes]) -> list[list[float]]:
        """Return one vector per input image (PNG/JPEG bytes)."""
        ...

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, in the SAME space as images."""
        ...
