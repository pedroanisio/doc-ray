"""Adapter: a sentence-transformers :class:`~doc_ray.ports.text_encoder.TextEncoder`.

Heavy, worker-side dependency. ``sentence_transformers`` (and its ``torch``
backend) are deliberately absent from the API image — like the vision stack —
so this module imports the library lazily and never at import time. The model is
selected via ``DOCRAY_SEMANTIC_MODEL`` and must be a 768-dimension
sentence-transformer to match the ``embedding.vector(768)`` column.

The query path (``semanticSearchSentences``) and the write path
(``semantic_embed`` job) both build their encoder through :func:`build_text_encoder`,
which fails typed-and-actionable when the stack is missing rather than leaking a
raw ``ModuleNotFoundError`` — the stable ``"not installed"`` marker the MCP layer
classifies on.

PALS posture: encoder output is ranking-only and asserts no fact, so there is no
verification gate (see :mod:`doc_ray.ports.text_encoder`).
"""

from __future__ import annotations

import importlib.util
import os

#: The default learned model and its honest provenance tag. The tag is recorded
#: as ``embedding.model`` on every row and is what the reader filters on.
DEFAULT_MODEL_NAME = "sentence-transformers/all-mpnet-base-v2"
DEFAULT_MODEL_TAG = "doc-ray/sbert-mpnet-768"
DEFAULT_DIM = 768

#: Modules that must be importable to embed text. Probed before any heavy import.
_STACK_MODULES = ("sentence_transformers",)


class SemanticStackUnavailableError(RuntimeError):
    """The sentence-transformers stack is not installed on this host."""


class SentenceTransformerTextEncoder:
    """A learned sentence encoder backed by ``sentence-transformers``."""

    def __init__(self, model_name: str | None = None) -> None:
        """Configure the encoder; the model is loaded lazily on first embed."""
        self._model_name = model_name or os.environ.get("DOCRAY_SEMANTIC_MODEL", DEFAULT_MODEL_NAME)
        self.model_id = DEFAULT_MODEL_TAG
        self.dim = DEFAULT_DIM
        self._model: object | None = None

    def _ensure_model(self) -> object:
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # lazy: worker-only dep

            self._model = SentenceTransformer(self._model_name)
        return self._model

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Return one raw (un-normalized) vector per input text."""
        if not texts:
            return []
        model = self._ensure_model()
        vectors = model.encode(  # type: ignore[attr-defined]
            list(texts), convert_to_numpy=True, normalize_embeddings=False
        )
        return [[float(x) for x in row] for row in vectors]


def build_text_encoder(model_name: str | None = None) -> SentenceTransformerTextEncoder:
    """Build the encoder, or raise a typed, actionable error if the stack is absent.

    Raises:
        SemanticStackUnavailableError: If ``sentence_transformers`` is not
            installed. The message keeps the stable ``"not installed"`` marker so
            the MCP boundary can classify the failure and stop retrying.
    """
    missing = [m for m in _STACK_MODULES if importlib.util.find_spec(m) is None]
    if missing:
        raise SemanticStackUnavailableError(
            "semantic sentence embedding is unavailable on this deployment: "
            f"module(s) {', '.join(missing)} are not installed on this host. Install the "
            "semantic stack (sentence-transformers) in the worker image to embed and in the "
            "API image to run semanticSearchSentences, or use a deployment that serves it."
        )
    return SentenceTransformerTextEncoder(model_name=model_name)
