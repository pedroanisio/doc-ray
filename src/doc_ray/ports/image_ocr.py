"""Port: OCR the text visible inside an image (Prism Tier-3 grounding aid).

The Tier-3 grounding gate verifies a VLM caption against the document's own
words. For figures on image-only or scanned pages, the *extracted* page text is
sparse, so a faithful caption of the figure's labels has little to ground
against. Deterministically OCR-ing the crop itself gives the gate an independent,
model-free transcription of the labels actually present in the image: a caption
whose specific terms appear in the OCR is grounded *in the image*, while a
hallucinated label the OCR never read stays ungrounded. The VLM never sees this
OCR text, so it cannot be gamed — it is verification evidence, not a prompt.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ImageOcr(Protocol):
    """Extract text visible in a raster image given its encoded bytes."""

    def ocr_image(self, image_bytes: bytes) -> str:
        """Return the OCR-read text of *image_bytes* (empty string if none/on failure)."""
        ...
