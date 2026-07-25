"""Unit regressions for bounded Prism Tier-2 embedding batches."""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any, cast

import pytest

from doc_ray.application.visual_embed import embed_document_visuals


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _Copy:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def __enter__(self) -> _Copy:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def write_row(self, row: tuple[Any, ...]) -> None:
        self._rows.append(row)


class _WriteCursor:
    def __init__(self, copies: dict[str, list[tuple[Any, ...]]]) -> None:
        self._copies = copies

    def __enter__(self) -> _WriteCursor:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def copy(self, statement: str) -> _Copy:
        table = statement.split()[1]
        return _Copy(self._copies.setdefault(table, []))


class _Conn:
    def __init__(self, count: int = 5) -> None:
        self.images = [
            {"id": f"image-{index}", "object_key": f"image-{index}.png"} for index in range(count)
        ]
        self.sentences = [
            {"id": f"sentence-{index}", "text": f"Sentence {index}."} for index in range(count)
        ]
        self.copies: dict[str, list[tuple[Any, ...]]] = {}

    def execute(self, statement: str, _params: object = None) -> _Rows:
        if "FROM document_image" in statement:
            return _Rows(self.images)
        if "FROM sentences" in statement:
            return _Rows(self.sentences)
        return _Rows([])

    def transaction(self) -> nullcontext[None]:
        return nullcontext()

    def cursor(self) -> _WriteCursor:
        return _WriteCursor(self.copies)


class _Store:
    def get(self, key: str) -> bytes:
        return key.encode()


class _RecordingEncoder:
    model_id = "recording-clip"
    dim = 3

    def __init__(self) -> None:
        self.image_batches: list[list[bytes]] = []
        self.text_batches: list[list[str]] = []

    def embed_images(self, images: list[bytes]) -> list[list[float]]:
        self.image_batches.append(images)
        return [[float(index + 1), 1.0, 1.0] for index, _image in enumerate(images)]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.text_batches.append(texts)
        return [[float(index + 1), 1.0, 1.0] for index, _text in enumerate(texts)]


def test_large_document_embeddings_use_bounded_order_preserving_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A large document never reaches the encoder as one unbounded GPU batch."""
    monkeypatch.setenv("DOCRAY_VISION_BATCH_SIZE", "2")
    conn = _Conn(count=5)
    encoder = _RecordingEncoder()

    result = embed_document_visuals(
        cast("Any", conn), cast("Any", _Store()), "document-1", encoder=encoder
    )

    assert [len(batch) for batch in encoder.image_batches] == [2, 2, 1]
    assert [len(batch) for batch in encoder.text_batches] == [2, 2, 1]
    assert [item for batch in encoder.image_batches for item in batch] == [
        f"image-{index}.png".encode() for index in range(5)
    ]
    assert result == {
        "model": "recording-clip",
        "images_embedded": 5,
        "sentences_embedded": 5,
    }
    assert len(conn.copies["image_embedding"]) == 5
    assert len(conn.copies["sentence_vision_embedding"]) == 5


@pytest.mark.parametrize("value", ["0", "-1", "not-an-integer"])
def test_visual_batch_size_rejects_invalid_configuration(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Invalid deployment tuning fails before any image bytes are loaded."""
    monkeypatch.setenv("DOCRAY_VISION_BATCH_SIZE", value)

    with pytest.raises(ValueError, match="DOCRAY_VISION_BATCH_SIZE"):
        embed_document_visuals(
            cast("Any", _Conn()),
            cast("Any", _Store()),
            "document-1",
            encoder=_RecordingEncoder(),
        )
