"""DB integration: Tier-3 captioning gates + routes correctly, and is idempotent.

Locks the review-hardening fixes: the VLM is never shown the grounding evidence
(non-gameable), grounding is page-local, a grounded+specific caption commits, a
generic one routes to visual_review, and a re-run clears the prior review queue.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
from typing import Any

import _itdb
from PIL import Image

from doc_ray.application.visual_caption import caption_document_images
from doc_ray.domain.ulid import new_ulid_str
from doc_ray.ports.vlm import VLMOutput


class _RecordingVLM:
    """A fake VLM that records the evidence it was given and returns a fixed caption."""

    model_id = "fake-vlm"
    model_version = "1.0"

    def __init__(self, caption: str) -> None:
        self._caption = caption
        self.seen_evidence: list[str] = []

    def caption_image(self, image_bytes: bytes, evidence_text: str) -> VLMOutput:
        self.seen_evidence.append(evidence_text)
        return VLMOutput(caption=self._caption, analysis={"note": "x"})


class _FakeStore:
    bucket = "it-caption"

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


def _crop() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (24, 24), (200, 40, 40)).save(buf, "PNG")
    return buf.getvalue()


def _add_image(conn: Any, doc_id: str, store: _FakeStore) -> None:
    crop = _crop()
    key = "document-images/x/p1.png"
    store.put(key, crop, "image/png")
    conn.execute(
        "INSERT INTO document_image (id, document_id, page_number, source, width_px, height_px, "
        "object_key, sha256, detector, detector_version) "
        "VALUES (%s, %s, 1, 'full-page', 24, 24, %s, %s, 'test', '1.0')",
        (new_ulid_str(), doc_id, key, hashlib.sha256(crop).hexdigest()),
    )


def _count(conn: Any, table: str, doc_id: str) -> int:
    return int(
        conn.execute(f"SELECT count(*) n FROM {table} WHERE document_id=%s", (doc_id,)).fetchone()[
            "n"
        ]
    )


def test_grounded_specific_caption_commits_and_vlm_never_sees_evidence(pg: Any) -> None:
    conn, created = pg
    doc_id = _itdb.make_doc(conn, "The Airbus A330 climbed to 38000 feet over the Atlantic.")
    created.append(doc_id)
    # Give the doc's spans page geometry so page-local evidence exists for page 1.
    conn.execute(
        "UPDATE spans SET page_number = 1, bbox_x0 = 0, bbox_y0 = 0, bbox_x1 = 100, "
        "bbox_y1 = 100 WHERE document_id = %s",
        (doc_id,),
    )
    store = _FakeStore({})
    _add_image(conn, doc_id, store)

    vlm = _RecordingVLM("Airbus A330 at 38000 feet over the Atlantic")
    result = caption_document_images(conn, store, doc_id, vlm=vlm, threshold=0.8)

    assert result["committed"] == 1 and result["review"] == 0
    assert vlm.seen_evidence == [""]  # PALS: the model was NOT shown the grounding text
    row = conn.execute(
        "SELECT status, grounding_score FROM visual_caption WHERE document_id=%s", (doc_id,)
    ).fetchone()
    assert row["status"] == "committed" and row["grounding_score"] >= 0.8


def test_generic_caption_routes_to_review_and_reruns_are_idempotent(pg: Any) -> None:
    conn, created = pg
    doc_id = _itdb.make_doc(conn, "The Airbus A330 climbed to 38000 feet over the Atlantic.")
    created.append(doc_id)
    conn.execute(
        "UPDATE spans SET page_number = 1, bbox_x0 = 0, bbox_y0 = 0, bbox_x1 = 100, "
        "bbox_y1 = 100 WHERE document_id = %s",
        (doc_id,),
    )
    store = _FakeStore({})
    _add_image(conn, doc_id, store)

    vlm = _RecordingVLM("a picture of something")
    result = caption_document_images(conn, store, doc_id, vlm=vlm, threshold=0.8)
    assert result["review"] == 1 and result["committed"] == 0
    assert _count(conn, "visual_review", doc_id) == 1

    # Re-run: the prior review row is cleared, not duplicated.
    caption_document_images(conn, store, doc_id, vlm=vlm, threshold=0.8)
    assert _count(conn, "visual_review", doc_id) == 1
    assert _count(conn, "visual_caption", doc_id) == 1
