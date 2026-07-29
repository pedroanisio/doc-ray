"""Prism Tier-3 orchestrator: grounded VLM captions with the PALS gate.

For each of a document's images it asks the VLM for a caption, grounds that
caption against the document's own text (``visual_grounding``), and gates on the
specificity-weighted score: at/above threshold the caption commits to
``visual_caption`` with recorded confidence; below threshold it is written as a
``review`` caption AND routed to the ``visual_review`` queue — it is NEVER
exposed as trusted. Only ``status='committed'`` captions are trusted output.

⚠ ARCHITECTURAL CONTRACT (PALS's LAW) — LLM OUTPUT IS UNVERIFIED BY DEFAULT.
The VLM caption is untrusted model output; this module is the mandatory
verification layer. The grounding gate here is not optional post-processing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

import psycopg

from doc_ray.application.resolution import AUTO_COMMIT_THRESHOLD
from doc_ray.application.visual_grounding import GroundingResult, grounding_score
from doc_ray.domain.progress import report_progress
from doc_ray.domain.ulid import new_ulid_str
from doc_ray.ports.image_ocr import ImageOcr
from doc_ray.ports.object_store import ObjectStore
from doc_ray.ports.vlm import VisionLanguageModel, VLMOutput

STATUS_COMMITTED = "committed"
STATUS_REVIEW = "review"
STATUS_REJECTED = "rejected"


@dataclass(frozen=True)
class CaptionDecision:
    """The gated verdict for one VLM caption."""

    status: str
    grounding: GroundingResult


def gate_caption(
    output: VLMOutput, evidence_text: str, *, threshold: float = AUTO_COMMIT_THRESHOLD
) -> CaptionDecision:
    """Verify + ground a VLM caption and decide commit / review / reject (pure).

    Empty/malformed captions are rejected outright. Otherwise the specificity-
    weighted grounding score decides: at/above ``threshold`` and specific enough
    -> committed; anything else -> review (never trusted on the low path).
    """
    caption = (output.caption or "").strip()
    if not caption:
        return CaptionDecision(STATUS_REJECTED, grounding_score("", evidence_text))
    grounding = grounding_score(caption, evidence_text)
    committed = grounding.score >= threshold and not grounding.insufficient_specificity
    return CaptionDecision(STATUS_COMMITTED if committed else STATUS_REVIEW, grounding)


def _grounded_against(decision: CaptionDecision) -> dict[str, Any]:
    return {
        "score": decision.grounding.score,
        "matched_terms": list(decision.grounding.matched_terms),
        "specific_terms": decision.grounding.specific_terms,
        "insufficient_specificity": decision.grounding.insufficient_specificity,
    }


def caption_document_images(
    conn: psycopg.Connection[Any],
    store: ObjectStore,
    document_id: str,
    *,
    vlm: VisionLanguageModel,
    image_ocr: ImageOcr | None = None,
    threshold: float = AUTO_COMMIT_THRESHOLD,
    resume: bool = True,
) -> dict[str, Any]:
    """Caption + ground + gate every image of a document. Idempotent per model.

    ``image_ocr`` (optional) supplies a deterministic OCR transcription of each
    crop that is *added to the grounding evidence* — never to the VLM prompt. It
    lets a faithful caption of a figure's own labels ground against those labels
    (crucial for figures on image-only/scanned pages, whose extracted page text
    is sparse), while a hallucinated label the OCR never read stays ungrounded.

    Each image commits in its OWN transaction, so a failure part-way through a
    document keeps every caption already paid for. ``resume`` (the default) then
    skips images this model has already captioned, making a retry cost only the
    remainder: captions are billed per request by hosted endpoints, and a
    document-wide rollback re-buys the whole book on every attempt. Pass
    ``resume=False`` for a genuine re-caption (new threshold, changed OCR
    evidence, same model id) — that re-runs every image and replaces its rows.
    """
    model = vlm.model_id
    images = conn.execute(
        "SELECT id, object_key, page_number FROM document_image WHERE document_id = %s ORDER BY id",
        (document_id,),
    ).fetchall()
    already_captioned: set[str] = set()
    if resume:
        already_captioned = {
            str(row["document_image_id"])
            for row in conn.execute(
                "SELECT vc.document_image_id FROM visual_caption vc "
                "WHERE vc.document_id = %s AND vc.model = %s",
                (document_id, model),
            ).fetchall()
        }
    # Figure-local evidence: sentences on the SAME page as each image (via span
    # geometry), so grounding compares a caption against its figure's own prose,
    # not the whole document (proposal §5.4). Pages without geometry/sentences get
    # empty evidence -> low grounding -> review, which is the safe direction.
    page_evidence: dict[int, str] = {}
    for row in conn.execute(
        "SELECT sp.page_number AS page, s.text AS text "
        "FROM sentences s JOIN spans sp ON sp.id = s.span_id "
        "WHERE s.document_id = %s AND sp.page_number IS NOT NULL "
        "ORDER BY sp.page_number, s.ordinal",
        (document_id,),
    ).fetchall():
        page = int(row["page"])
        page_evidence[page] = f"{page_evidence.get(page, '')} {row['text']}".strip()

    committed = review = rejected = 0
    skipped = 0
    for index, image in enumerate(images):
        report_progress(index, len(images) or 1, "Captioning images")
        image_id = str(image["id"])
        if image_id in already_captioned:
            skipped += 1
            continue
        crop_bytes = store.get(str(image["object_key"]))
        # Grounding evidence = the figure's page prose PLUS a deterministic OCR
        # of the crop itself. OCR corroborates labels a faithful caption
        # transcribes even when the page has little extractable text; it is
        # NOT shown to the VLM, so it cannot be gamed.
        page_text = page_evidence.get(int(image["page_number"]), "")
        ocr_text = image_ocr.ocr_image(crop_bytes) if image_ocr is not None else ""
        evidence_text = f"{page_text} {ocr_text}".strip()
        # The VLM captions from the image ALONE — it is never shown the
        # grounding evidence, so grounding cannot be gamed by echoing it.
        # Billed work happens HERE, outside the write transaction, so the caption
        # it produces is committed below before the next request can fail.
        output = vlm.caption_image(crop_bytes, "")
        decision = gate_caption(output, evidence_text, threshold=threshold)
        grounded = _grounded_against(decision)
        # Per-IMAGE transaction. Replacing prior output is scoped to this image
        # (visual_caption is UNIQUE on document_image_id+model), so a re-caption
        # stays idempotent and never leaves orphaned pending reviews, while a
        # failure on a later image cannot roll this one back.
        with conn.transaction():
            conn.execute(
                "DELETE FROM visual_caption WHERE document_image_id = %s AND model = %s",
                (image_id, model),
            )
            conn.execute("DELETE FROM visual_review WHERE document_image_id = %s", (image_id,))
            conn.execute(
                "INSERT INTO visual_caption "
                "(id, document_image_id, document_id, caption, analysis, "
                "model, model_version, grounding_score, grounded_against, status) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    new_ulid_str(),
                    image_id,
                    document_id,
                    output.caption,
                    json.dumps(output.analysis),
                    model,
                    vlm.model_version,
                    decision.grounding.score,
                    json.dumps(grounded),
                    decision.status,
                ),
            )
            if decision.status == STATUS_REVIEW:
                conn.execute(
                    "INSERT INTO visual_review (id, document_image_id, document_id, proposed, "
                    "grounding_score, grounding_evidence, status) "
                    "VALUES (%s, %s, %s, %s, %s, %s, 'pending')",
                    (
                        new_ulid_str(),
                        image_id,
                        document_id,
                        json.dumps({"caption": output.caption, "analysis": output.analysis}),
                        decision.grounding.score,
                        json.dumps(grounded),
                    ),
                )
        if decision.status == STATUS_COMMITTED:
            committed += 1
        elif decision.status == STATUS_REVIEW:
            review += 1
        else:
            rejected += 1

    return cast(
        "dict[str, Any]",
        {
            "model": model,
            "committed": committed,
            "review": review,
            "rejected": rejected,
            "skipped": skipped,
        },
    )
