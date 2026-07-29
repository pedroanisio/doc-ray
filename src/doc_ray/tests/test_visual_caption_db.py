"""DB integration: Tier-3 captioning gates + routes correctly, and is idempotent.

Locks the review-hardening fixes: the VLM is never shown the grounding evidence
(non-gameable), grounding is page-local, a grounded+specific caption commits, a
generic one routes to visual_review, and a re-run clears the prior review queue.

Also locks the caption-durability contract: every image's caption commits on its
own, so a failure at image N keeps the N-1 already-paid captions and the retry
resumes instead of re-buying them.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
from typing import Any

import _itdb
import pytest
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


class _CountingVLM:
    """Records how many images it was actually asked to caption."""

    model_id = "fake-vlm"
    model_version = "1.0"

    def __init__(self, caption: str) -> None:
        self._caption = caption
        self.calls = 0

    def caption_image(self, image_bytes: bytes, evidence_text: str) -> VLMOutput:
        self.calls += 1
        return VLMOutput(caption=self._caption, analysis={"note": "x"})


class _FailingVLM:
    """Captions ``fail_after`` images, then raises — the hosted-endpoint 429 shape."""

    model_id = "fake-vlm"
    model_version = "1.0"

    def __init__(self, caption: str, fail_after: int) -> None:
        self._caption = caption
        self._fail_after = fail_after
        self.calls = 0

    def caption_image(self, image_bytes: bytes, evidence_text: str) -> VLMOutput:
        if self.calls >= self._fail_after:
            raise RuntimeError("HTTP Error 429: Too Many Requests")
        self.calls += 1
        return VLMOutput(caption=self._caption, analysis={"note": "x"})


class _FakeOcr:
    """A fake ImageOcr returning a fixed transcription for every crop."""

    def __init__(self, text: str) -> None:
        self._text = text

    def ocr_image(self, image_bytes: bytes) -> str:
        return self._text


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


def _crop(ordinal: int = 1) -> bytes:
    # Distinct pixels per ordinal: document_image is UNIQUE on
    # (document_id, page_number, source, sha256), so identical crops collide.
    buf = BytesIO()
    Image.new("RGB", (24, 24), (200, 40, ordinal % 256)).save(buf, "PNG")
    return buf.getvalue()


def _add_image(conn: Any, doc_id: str, store: _FakeStore, ordinal: int = 1) -> None:
    crop = _crop(ordinal)
    key = f"document-images/x/p{ordinal}.png"
    store.put(key, crop, "image/png")
    conn.execute(
        "INSERT INTO document_image (id, document_id, page_number, source, width_px, height_px, "
        "object_key, sha256, detector, detector_version) "
        "VALUES (%s, %s, 1, 'full-page', 24, 24, %s, %s, 'test', '1.0')",
        (new_ulid_str(), doc_id, key, hashlib.sha256(crop).hexdigest()),
    )


def _add_images(conn: Any, doc_id: str, store: _FakeStore, count: int) -> None:
    for ordinal in range(1, count + 1):
        _add_image(conn, doc_id, store, ordinal)


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

    # Forced re-run: the prior review row is cleared, not duplicated.
    caption_document_images(conn, store, doc_id, vlm=vlm, threshold=0.8, resume=False)
    assert _count(conn, "visual_review", doc_id) == 1
    assert _count(conn, "visual_caption", doc_id) == 1

    # Resumed re-run: nothing left to do, and still no duplicates.
    resumed = caption_document_images(conn, store, doc_id, vlm=vlm, threshold=0.8)
    assert resumed["skipped"] == 1
    assert _count(conn, "visual_review", doc_id) == 1
    assert _count(conn, "visual_caption", doc_id) == 1


def _doc_with_images(conn: Any, created: list[str], store: _FakeStore, count: int) -> str:
    doc_id = _itdb.make_doc(conn, "The Airbus A330 climbed to 38000 feet over the Atlantic.")
    created.append(doc_id)
    conn.execute(
        "UPDATE spans SET page_number = 1, bbox_x0 = 0, bbox_y0 = 0, bbox_x1 = 100, "
        "bbox_y1 = 100 WHERE document_id = %s",
        (doc_id,),
    )
    _add_images(conn, doc_id, store, count)
    return doc_id


def test_captions_paid_for_before_a_midrun_failure_are_not_discarded(pg: Any) -> None:
    """A failure at image N keeps the N-1 captions already bought.

    Regression: the orchestrator used to wrap the whole document in ONE
    transaction opening with ``DELETE FROM visual_caption``, so a 429 on the last
    image rolled back every caption already billed by the endpoint. On a
    thousand-image book that discarded hours of paid tokens per retry.
    """
    conn, created = pg
    store = _FakeStore({})
    doc_id = _doc_with_images(conn, created, store, 4)

    vlm = _FailingVLM("Airbus A330 at 38000 feet over the Atlantic", fail_after=2)
    with pytest.raises(RuntimeError, match="429"):
        caption_document_images(conn, store, doc_id, vlm=vlm, threshold=0.8)

    assert vlm.calls == 2
    assert _count(conn, "visual_caption", doc_id) == 2


def test_retry_after_a_failure_resumes_and_never_re_buys_a_caption(pg: Any) -> None:
    """The retry captions only what is missing — the money-preserving half."""
    conn, created = pg
    store = _FakeStore({})
    doc_id = _doc_with_images(conn, created, store, 4)
    caption = "Airbus A330 at 38000 feet over the Atlantic"

    with pytest.raises(RuntimeError, match="429"):
        caption_document_images(
            conn, store, doc_id, vlm=_FailingVLM(caption, fail_after=2), threshold=0.8
        )
    assert _count(conn, "visual_caption", doc_id) == 2

    retry = _CountingVLM(caption)
    result = caption_document_images(conn, store, doc_id, vlm=retry, threshold=0.8)

    assert retry.calls == 2, "the retry must not re-caption images already paid for"
    assert result["skipped"] == 2
    assert _count(conn, "visual_caption", doc_id) == 4


def test_resume_false_forces_a_full_recaption_without_duplicating_rows(pg: Any) -> None:
    """An explicit re-caption still re-runs every image and stays idempotent."""
    conn, created = pg
    store = _FakeStore({})
    doc_id = _doc_with_images(conn, created, store, 3)
    caption = "Airbus A330 at 38000 feet over the Atlantic"

    caption_document_images(conn, store, doc_id, vlm=_CountingVLM(caption), threshold=0.8)
    assert _count(conn, "visual_caption", doc_id) == 3

    forced = _CountingVLM(caption)
    result = caption_document_images(conn, store, doc_id, vlm=forced, threshold=0.8, resume=False)

    assert forced.calls == 3 and result["skipped"] == 0
    assert _count(conn, "visual_caption", doc_id) == 3


def test_a_failed_run_leaves_no_orphaned_review_rows(pg: Any) -> None:
    """Review rows stay in lockstep with captions across a failure + resume."""
    conn, created = pg
    store = _FakeStore({})
    doc_id = _doc_with_images(conn, created, store, 4)

    with pytest.raises(RuntimeError, match="429"):
        caption_document_images(
            conn,
            store,
            doc_id,
            vlm=_FailingVLM("a picture of something", fail_after=2),
            threshold=0.8,
        )
    assert _count(conn, "visual_caption", doc_id) == 2
    assert _count(conn, "visual_review", doc_id) == 2

    caption_document_images(
        conn, store, doc_id, vlm=_CountingVLM("a picture of something"), threshold=0.8
    )
    assert _count(conn, "visual_caption", doc_id) == 4
    assert _count(conn, "visual_review", doc_id) == 4


def test_ocr_of_crop_lifts_grounding_when_page_text_is_sparse(pg: Any) -> None:
    """A figure caption unsupported by page prose commits once OCR of the crop
    supplies the same labels — the OCR-grounding lever for image-only pages."""
    conn, created = pg
    # Page prose is unrelated to the figure's labels (as on a scanned/figure page).
    doc_id = _itdb.make_doc(conn, "This chapter is about music.")
    created.append(doc_id)
    conn.execute(
        "UPDATE spans SET page_number = 1, bbox_x0 = 0, bbox_y0 = 0, bbox_x1 = 100, "
        "bbox_y1 = 100 WHERE document_id = %s",
        (doc_id,),
    )
    store = _FakeStore({})
    _add_image(conn, doc_id, store)
    caption = "GTHIRD Tonnetz C E G B D F A"

    # Without OCR: the labels aren't in the page prose -> held for review.
    without = caption_document_images(
        conn, store, doc_id, vlm=_RecordingVLM(caption), threshold=0.8
    )
    assert without["committed"] == 0 and without["review"] == 1

    # With OCR of the crop supplying those labels -> grounded, so it commits.
    # resume=False: re-grounding the SAME model is an explicit re-caption, not a
    # resume, so it must re-run the image rather than skip it as already done.
    vlm = _RecordingVLM(caption)
    with_ocr = caption_document_images(
        conn, store, doc_id, vlm=vlm, image_ocr=_FakeOcr(caption), threshold=0.8, resume=False
    )
    assert with_ocr["committed"] == 1 and with_ocr["review"] == 0
    # PALS preserved: the VLM still captions the image alone, never the OCR text.
    assert vlm.seen_evidence == [""]
    row = conn.execute(
        "SELECT status, grounding_score FROM visual_caption WHERE document_id=%s", (doc_id,)
    ).fetchone()
    assert row["status"] == "committed" and row["grounding_score"] >= 0.8
