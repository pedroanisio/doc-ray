"""Prism Tier-3 orchestrator: grounded VLM captions, verified by retraction.

For each of a document's images it asks the VLM for a caption and grounds that
caption against the document's own text (``visual_grounding``). A structurally
valid caption is COMMITTED on write and appears in the reader, labelled with its
model and its grounding score; a human who can see the image retracts one that is
wrong, and the retraction is recorded against a principal.

⚠ ARCHITECTURAL CONTRACT (PALS's LAW) — LLM OUTPUT IS UNVERIFIED BY DEFAULT.
The VLM caption is untrusted model output and this module is still its mandatory
verification layer. What changed (BL-201) is WHERE the human decision happens,
not WHETHER one is required:

* Verification was pre-publication and never happened. Of 6,122 proposals, 21
  were ever committed; 5,851 sat in a queue at 25 items a page. A control no
  operator can exercise is not a control, and the honest description of the old
  behaviour is that captions were withheld from everyone forever.
* Verification is now post-publication and situated. The reviewer sees the image,
  the caption and the score together in the reader — strictly more context than
  the review queue gave — and a retraction writes ``retracted_by`` /
  ``retracted_at`` / ``retraction_reason`` (migration 150). The audit trail is a
  superset of what approval recorded.
* The claim is never presented as doc-ray's own. The reader marks it as a model
  proposal and shows the grounding score beside it, so a reader can discount it
  without waiting for anyone.

This is a deliberate trade of pre-publication certainty for coverage, and it is
reversible per deployment: raise ``DOCRAY_CAPTION_APPROVE_FLOOR`` and captions
below that score go back to the review queue instead of the reader. Setting it to
1.0 restores a fully pre-publication gate.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, cast

import psycopg

from doc_ray.application.resolution import AUTO_COMMIT_THRESHOLD
from doc_ray.application.visual_grounding import GroundingResult, grounding_score
from doc_ray.domain.caption_text import is_structurally_invalid, sanitize_caption
from doc_ray.domain.progress import report_progress
from doc_ray.domain.ulid import new_ulid_str
from doc_ray.ports.image_ocr import ImageOcr
from doc_ray.ports.object_store import ObjectStore
from doc_ray.ports.vlm import VisionLanguageModel, VLMOutput

#: Env var setting the grounding score at/above which a caption is published
#: to the reader without a pre-publication human decision.
APPROVE_FLOOR_ENV = "DOCRAY_CAPTION_APPROVE_FLOOR"

#: Approve everything structurally valid. The pre-publication gate did not
#: work at corpus scale — 21 of 6,122 captions were ever committed — so the
#: flow inverts: publish, show the grounding score beside the claim, and let a
#: reader who can see the image retract it (BL-201). Raise this to send weak
#: captions back to the review queue instead of the reader.
DEFAULT_APPROVE_FLOOR = 0.0

STATUS_COMMITTED = "committed"
STATUS_REVIEW = "review"
STATUS_REJECTED = "rejected"


@dataclass(frozen=True)
class CaptionDecision:
    """The gated verdict for one VLM caption.

    ``caption`` is the sanitised text — the same string that was grounded, so a
    caller persists what was actually scored rather than the raw model output.
    """

    status: str
    grounding: GroundingResult
    caption: str = ""


def _approve_floor_from_env() -> float:
    """Read the configured approval floor, failing closed on a malformed value."""
    raw = os.environ.get(APPROVE_FLOOR_ENV, "").strip()
    if not raw:
        return DEFAULT_APPROVE_FLOOR
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{APPROVE_FLOOR_ENV} must be a number in [0, 1]; got {raw!r}") from None
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{APPROVE_FLOOR_ENV} must be in [0, 1]; got {value}")
    return value


def gate_caption(
    output: VLMOutput,
    evidence_text: str,
    *,
    threshold: float = AUTO_COMMIT_THRESHOLD,
    approve_floor: float | None = None,
) -> CaptionDecision:
    """Verify + ground a VLM caption and decide commit / review / reject (pure).

    The caption is sanitised first: a reasoning-capable VLM emits its
    deliberation and chat-template sentinels inline, and scoring that scaffolding
    as if it were the caption drags an otherwise sound caption's grounding toward
    zero. Sanitising before grounding means the score describes the caption.

    Structurally invalid captions — empty, or too short to be a caption at all
    once scaffolding is removed — are rejected outright rather than queued, so a
    human is never asked to adjudicate token soup (BL-196). That is a judgement
    about the *string*, not about whether the caption is true; truth is what
    grounding measures, and a well-formed caption that grounds poorly still goes
    to review.

    Otherwise the caption is COMMITTED when its grounding score is at or above
    ``approve_floor`` (default 0.0, i.e. everything structurally valid), and
    queued for review only below it.

    This inverts the original gate deliberately (BL-201). Withholding until a
    human approved did not produce caution, it produced silence: 21 of 6,122
    captions were ever committed while 5,851 sat in a queue no operator could
    clear. Verification moves to the reader, which shows the grounding score
    beside the claim and offers a retraction that is recorded against a
    principal — so the audit trail is a superset of what approval recorded.

    ``threshold`` is retained for callers that still want the old
    specificity-gated commit decision; it no longer governs this path.
    """
    floor = _approve_floor_from_env() if approve_floor is None else approve_floor
    if not 0.0 <= floor <= 1.0:
        raise ValueError(f"approve_floor must be in [0, 1]; got {floor}")
    caption = sanitize_caption(output.caption)
    if is_structurally_invalid(caption):
        return CaptionDecision(STATUS_REJECTED, grounding_score("", evidence_text), caption)
    grounding = grounding_score(caption, evidence_text)
    status = STATUS_COMMITTED if grounding.score >= floor else STATUS_REVIEW
    return CaptionDecision(status, grounding, caption)


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
                    decision.caption,
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
                        json.dumps({"caption": decision.caption, "analysis": output.analysis}),
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
