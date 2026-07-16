"""Port: a vision-language model that captions an image (Prism Tier-3).

The only tier that produces untrusted model output. The concrete adapter (a 7B
image-text VLM served on the GPU, or a hosted batch API) implements this port;
its output is NEVER trusted directly — the application grounds it against the
document's own figure text and gates on the score before anything is committed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class VLMOutput:
    """One VLM caption + structured analysis for an image (untrusted)."""

    caption: str
    analysis: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class VisionLanguageModel(Protocol):
    """Caption an image, optionally conditioned on the document's figure text."""

    model_id: str
    model_version: str

    def caption_image(self, image_bytes: bytes, evidence_text: str) -> VLMOutput:
        """Return a caption + analysis for *image_bytes* (untrusted output)."""
        ...
