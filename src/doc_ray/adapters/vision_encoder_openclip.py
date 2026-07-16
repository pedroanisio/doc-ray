"""Adapter: open_clip dual-encoder implementation of :class:`VisionEncoder`.

The production Tier-2 encoder. Lazily loads an open_clip CLIP/SigLIP model onto
the GPU on first use (the model weights and ``open_clip``/``torch`` are only
required where this actually runs — the H200 worker — not for import). Returns
L2-normalized image/text vectors in one shared space.

Not exercised by the unit suite (no model/weights in CI); the orchestrator and
cross-modal search are tested with a deterministic fake encoder instead. Select
the model via ``DOCRAY_VISION_MODEL`` / ``DOCRAY_VISION_PRETRAINED``.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any, cast

from doc_ray.ports.vision_encoder import VisionEncoder


class OpenClipVisionEncoder(VisionEncoder):
    """Embed images/text with an open_clip dual encoder (GPU, lazily loaded)."""

    def __init__(
        self,
        model_name: str = "ViT-L-14",
        pretrained: str = "openai",
        *,
        dim: int = 768,
        device: str | None = None,
    ) -> None:
        """Configure the encoder (weights load lazily on first embed call)."""
        self.model_id = f"open_clip/{model_name}/{pretrained}"
        self.dim = dim
        self._model_name = model_name
        self._pretrained = pretrained
        self._device = device
        self._model: Any = None
        self._preprocess: Any = None
        self._tokenizer: Any = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        import open_clip  # type: ignore[import-not-found]
        import torch

        device = self._device or ("cuda" if torch.cuda.is_available() else "cpu")
        model, _, preprocess = open_clip.create_model_and_transforms(
            self._model_name, pretrained=self._pretrained, device=device
        )
        model.eval()
        self._model = model
        self._preprocess = preprocess
        self._tokenizer = open_clip.get_tokenizer(self._model_name)
        self._device = device

    def embed_images(self, images: list[bytes]) -> list[list[float]]:
        """Return L2-normalized image vectors."""
        import torch
        from PIL import Image

        self._ensure_loaded()
        with torch.no_grad():
            tensors = [
                self._preprocess(Image.open(BytesIO(data)).convert("RGB")) for data in images
            ]
            batch = torch.stack(tensors).to(self._device)
            feats = self._model.encode_image(batch)
            feats = feats / feats.norm(dim=-1, keepdim=True)
            return cast("list[list[float]]", feats.cpu().tolist())

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Return L2-normalized text vectors in the same space as images."""
        import torch

        self._ensure_loaded()
        with torch.no_grad():
            tokens = self._tokenizer(texts).to(self._device)
            feats = self._model.encode_text(tokens)
            feats = feats / feats.norm(dim=-1, keepdim=True)
            return cast("list[list[float]]", feats.cpu().tolist())
