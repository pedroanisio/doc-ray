"""DB integration: KIND_VISUAL_EMBED writes vision embeddings + cross-modal search.

Runs against the isolated test database with a deterministic fake encoder (no GPU
/ model). Verifies migration 091 (image_embedding + sentence_vision_embedding +
HNSW) applies, the pgvector COPY path works, and text->image search ranks the
document's images.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
from typing import Any

import _itdb
from PIL import Image

from doc_ray.application.visual_embed import cross_modal_image_search, embed_document_visuals
from doc_ray.domain.ulid import new_ulid_str


class _FakeEncoder:
    """Deterministic 768-d encoder — content-hash pseudo-embeddings, no model."""

    model_id = "fake-clip-768"
    dim = 768

    def _vec(self, data: bytes) -> list[float]:
        digest = hashlib.blake2b(data, digest_size=32).digest()
        return [((digest[i % 32] + i) % 255) / 255.0 for i in range(self.dim)]

    def embed_images(self, images: list[bytes]) -> list[list[float]]:
        return [self._vec(b) for b in images]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t.encode("utf-8")) for t in texts]


class _FakeStore:
    bucket = "it-embed"

    def __init__(self, blobs: dict[str, bytes]) -> None:
        self._blobs = dict(blobs)

    def get(self, key: str) -> bytes:
        return self._blobs[key]

    def put(self, key: str, data: bytes, content_type: str) -> str:
        self._blobs[key] = data
        return f"s3://{self.bucket}/{key}"

    def ensure_bucket(self) -> None: ...

    def list_objects(self, prefix: str = "") -> list[str]:
        return list(self._blobs)

    def delete(self, key: str) -> None:
        self._blobs.pop(key, None)


def _crop_png() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (24, 24), (30, 120, 200)).save(buf, "PNG")
    return buf.getvalue()


def test_visual_embed_writes_vectors_and_cross_modal_search(pg: Any) -> None:
    conn, created = pg
    doc_id = _itdb.make_doc(conn, "A red diagram. A second sentence about charts.")
    created.append(doc_id)

    crop = _crop_png()
    key = "document-images/x/p1.png"
    store = _FakeStore({key: crop})
    conn.execute(
        "INSERT INTO document_image "
        "(id, document_id, page_number, source, width_px, height_px, object_key, sha256, "
        " detector, detector_version) "
        "VALUES (%s, %s, 1, 'full-page', 24, 24, %s, %s, 'test', '1.0')",
        (new_ulid_str(), doc_id, key, hashlib.sha256(crop).hexdigest()),
    )

    encoder = _FakeEncoder()
    result = embed_document_visuals(conn, store, doc_id, encoder=encoder)
    assert result["images_embedded"] == 1
    assert result["sentences_embedded"] >= 2

    img_n = conn.execute(
        "SELECT count(*) n FROM image_embedding WHERE document_id=%s", (doc_id,)
    ).fetchone()["n"]
    sent_n = conn.execute(
        "SELECT count(*) n FROM sentence_vision_embedding WHERE document_id=%s", (doc_id,)
    ).fetchone()["n"]
    assert img_n == 1 and sent_n == result["sentences_embedded"]

    hits = cross_modal_image_search(conn, "charts and diagrams", encoder=encoder, limit=10)
    assert len(hits) >= 1
    assert str(hits[0]["document_id"]) == str(doc_id)
    assert -1.0001 <= float(hits[0]["similarity"]) <= 1.0001

    # Idempotent per model: re-embed replaces, does not accumulate.
    again = embed_document_visuals(conn, store, doc_id, encoder=encoder)
    img_n2 = conn.execute(
        "SELECT count(*) n FROM image_embedding WHERE document_id=%s", (doc_id,)
    ).fetchone()["n"]
    assert img_n2 == again["images_embedded"] == 1
