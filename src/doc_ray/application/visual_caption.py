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
    threshold: float = AUTO_COMMIT_THRESHOLD,
) -> dict[str, Any]:
    """Caption + ground + gate every image of a document. Idempotent per model."""
    model = vlm.model_id
    images = conn.execute(
        "SELECT id, object_key FROM document_image WHERE document_id = %s ORDER BY id",
        (document_id,),
    ).fetchall()
    evidence_rows = conn.execute(
        "SELECT text FROM sentences WHERE document_id = %s ORDER BY ordinal, id",
        (document_id,),
    ).fetchall()
    evidence_text = " ".join(str(r["text"]) for r in evidence_rows)

    committed = review = rejected = 0
    with conn.transaction():
        conn.execute(
            "DELETE FROM visual_caption WHERE document_id = %s AND model = %s",
            (document_id, model),
        )
        for image in images:
            report_progress(0, len(images) or 1, "Captioning images")
            output = vlm.caption_image(store.get(str(image["object_key"])), evidence_text)
            decision = gate_caption(output, evidence_text, threshold=threshold)
            grounded = _grounded_against(decision)
            conn.execute(
                "INSERT INTO visual_caption "
                "(id, document_image_id, document_id, caption, analysis, "
                "model, model_version, grounding_score, grounded_against, status) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    new_ulid_str(),
                    str(image["id"]),
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
            if decision.status == STATUS_COMMITTED:
                committed += 1
            elif decision.status == STATUS_REVIEW:
                review += 1
                conn.execute(
                    "INSERT INTO visual_review (id, document_image_id, document_id, proposed, "
                    "grounding_score, grounding_evidence, status) "
                    "VALUES (%s, %s, %s, %s, %s, %s, 'pending')",
                    (
                        new_ulid_str(),
                        str(image["id"]),
                        document_id,
                        json.dumps({"caption": output.caption, "analysis": output.analysis}),
                        decision.grounding.score,
                        json.dumps(grounded),
                    ),
                )
            else:
                rejected += 1

    return cast(
        "dict[str, Any]",
        {"model": model, "committed": committed, "review": review, "rejected": rejected},
    )
