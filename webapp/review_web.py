"""Corpus-level human review inbox for untrusted machine proposals.

Reads stay RLS-scoped through the webapp database role or GraphQL API. Every
decision crosses GraphQL so the API enforces write scope and records its audit
event; this module never commits a model proposal directly.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import ModuleType

from flask import current_app, redirect, request, url_for

_core: ModuleType
LANES = ("metadata", "entities", "visual")
PAGE_SIZE = 25

_METADATA_QUERY = """
query ReviewMetadata($documentId: ID!) {
  documentMetadataSuggestions(documentId: $documentId, status: "pending") {
    id documentId field currentValue suggestedValue confidence confidenceBand
    reason source status createdAt
  }
}
"""
_ENTITY_QUERY = """
query ReviewEntities($documentId: ID, $after: String) {
  resolutionReview(documentId: $documentId, status: "pending", limit: 25, after: $after) {
    id mentionId candidates proposedCanonicalId
    proposedCanonical { id preferred entityType }
    confidence reason createdAt cursor
  }
}
"""
_UNRESOLVED_QUERY = """
query ReviewUnresolved($documentId: ID!) {
  unresolvedMentions(documentId: $documentId, limit: 100) {
    mentionId surface entityType charStart charEnd cursor
  }
}
"""
_VISUAL_QUERY = """
query ReviewVisual($documentId: ID, $after: String) {
  visualReview(documentId: $documentId, status: "pending", limit: 25, after: $after) {
    id documentImageId documentId proposed groundingScore groundingEvidence status
    pageNumber objectKey createdAt cursor
  }
}
"""


def _pending_counts() -> dict:
    """Return small, indexed review-queue counts for the navigation badges."""
    row = _core.q1(
        """
        SELECT
          (SELECT count(*) FROM document_metadata_suggestion WHERE status = 'pending') metadata,
          (SELECT count(*) FROM seed_suggestion WHERE status = 'pending') seeds,
          (SELECT count(*) FROM resolution_review WHERE status = 'pending') resolutions,
          (SELECT count(*) FROM visual_review WHERE status = 'pending') visual
        """
    )
    return dict(row or {})


def _metadata_lane(document_id: str | None, page: int) -> dict:
    docs = _core.q(
        """
        SELECT d.id, d.title, count(*) pending_count
        FROM document_metadata_suggestion s
        JOIN documents d ON d.id = s.document_id
        WHERE s.status = 'pending'
        GROUP BY d.id, d.title
        ORDER BY min(s.created_at), d.id
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        {"limit": PAGE_SIZE, "offset": (page - 1) * PAGE_SIZE},
    )
    items = []
    if document_id:
        items = _core.gql(_METADATA_QUERY, {"documentId": document_id}).get(
            "documentMetadataSuggestions", []
        )
    return {"metadata_docs": docs, "metadata_items": items, "has_next": len(docs) == PAGE_SIZE}


def _mention_context(items: list[dict]) -> dict[str, dict]:
    mention_ids = [str(item["mentionId"]) for item in items]
    if not mention_ids:
        return {}
    rows = _core.q(
        """
        SELECT em.id mention_id, a.document_id, d.title
        FROM entity_mention em
        JOIN annotations a ON a.id = em.annotation_id
        JOIN documents d ON d.id = a.document_id
        WHERE em.id = ANY(%s::ulid[])
        """,
        (mention_ids,),
    )
    return {str(row["mention_id"]): row for row in rows}


def _entity_lane(document_id: str | None, after: str | None) -> dict:
    result = _core.gql(_ENTITY_QUERY, {"documentId": document_id, "after": after})
    reviews = list(result.get("resolutionReview") or [])
    contexts = _mention_context(reviews)
    for item in reviews:
        item["context"] = contexts.get(str(item["mentionId"]), {})
    seeds = _core.q(
        """
        SELECT s.id, s.document_id, d.title, s.term, s.source, s.description,
               s.occurrences, s.qid, s.label
        FROM seed_suggestion s JOIN documents d ON d.id = s.document_id
        WHERE s.status = 'pending' AND (%(doc)s::uuid IS NULL OR s.document_id = %(doc)s)
        ORDER BY s.occurrences DESC, s.id LIMIT 25
        """,
        {"doc": document_id},
    )
    unresolved = []
    if document_id:
        unresolved = _core.gql(_UNRESOLVED_QUERY, {"documentId": document_id}).get(
            "unresolvedMentions", []
        )
    return {
        "resolution_items": reviews,
        "seed_items": seeds,
        "unresolved_items": unresolved,
        "next_cursor": reviews[-1].get("cursor") if len(reviews) == PAGE_SIZE else None,
    }


def _grounding_band(score: float) -> str:
    if score >= 0.8:
        return "Strong grounding"
    if score >= 0.6:
        return "Moderate grounding"
    return "Weak grounding"


def _visual_lane(document_id: str | None, after: str | None) -> dict:
    result = _core.gql(_VISUAL_QUERY, {"documentId": document_id, "after": after})
    items = list(result.get("visualReview") or [])
    for item in items:
        item["groundingBand"] = _grounding_band(float(item.get("groundingScore") or 0))
        proposed = item.get("proposed")
        item["caption"] = (
            str(proposed.get("caption") or proposed.get("text") or proposed)
            if isinstance(proposed, Mapping)
            else str(proposed or "")
        )
    return {
        "visual_items": items,
        "next_cursor": items[-1].get("cursor") if len(items) == PAGE_SIZE else None,
    }


def review():
    """Render one review lane while preserving counts and explicit empty/error states."""
    lane = request.args.get("lane", "metadata")
    if lane not in LANES:
        lane = "metadata"
    document_id = request.args.get("document_id") or None
    page = max(1, request.args.get("page", 1, type=int))
    after = request.args.get("after") or None
    model: dict = {}
    error = request.args.get("error")
    try:
        if lane == "metadata":
            model.update(_metadata_lane(document_id, page))
        elif lane == "entities":
            model.update(_entity_lane(document_id, after))
        else:
            model.update(_visual_lane(document_id, after))
    except Exception as exc:  # noqa: BLE001 - API read errors belong in the inbox envelope
        current_app.logger.warning("review lane read failed: %s", exc)
        error = str(exc)
    try:
        counts = _pending_counts()
    except Exception as exc:  # noqa: BLE001 - counts never blank the decision surface
        current_app.logger.warning("review counts unavailable: %s", exc)
        counts = {}
    return _core.render_template(
        "review.html",
        lane=lane,
        lanes=LANES,
        document_id=document_id,
        page=page,
        counts=counts,
        notice=request.args.get("notice"),
        error=error,
        **model,
    )


def _batch_decision(kind: str, action: str, ids: list[str]) -> tuple[str, int]:
    field = "approveIds" if action == "approve" else "rejectIds"
    operations = {
        "metadata": "decideDocumentMetadataSuggestions",
        "seed": "decideSeeds",
        "visual": "decideVisualCaptions",
    }
    operation = operations.get(kind)
    if operation is None or action not in {"approve", "reject"}:
        raise ValueError("unknown review action")
    if not ids:
        raise ValueError("select at least one proposal")
    data = _core.gql(
        f"mutation($ids: [ID!]) {{ {operation}({field}: $ids) }}",
        {"ids": ids},
    )
    return operation, int(data[operation])


def _correction() -> str:
    variables = {
        "documentId": request.form["document_id"],
        "action": request.form["action"],
        "charStart": int(request.form["char_start"]),
        "charEnd": int(request.form["char_end"]),
        "entityType": request.form.get("entity_type") or None,
        "surface": request.form.get("surface") or None,
    }
    data = _core.gql(
        """mutation Correct($documentId: ID!, $action: String!, $charStart: Int!,
        $charEnd: Int!, $entityType: String, $surface: String) {
          correctEntityMention(documentId: $documentId, action: $action,
            charStart: $charStart, charEnd: $charEnd, entityType: $entityType,
            surface: $surface) { id }
        }""",
        variables,
    )
    return str(data["correctEntityMention"]["id"])


def review_action():
    """Dispatch one human decision through an audited GraphQL mutation."""
    lane = request.form.get("lane", "metadata")
    document_id = request.form.get("document_id") or None
    try:
        kind = request.form.get("kind", "")
        if kind == "correction":
            job_id = _correction()
            notice = f"correction job {job_id} queued"
        elif kind == "seed-run":
            data = _core.gql(
                "mutation($id: ID!) { seedApproved(documentId: $id) { id } }",
                {"id": document_id},
            )
            notice = f"seeding job {data['seedApproved']['id']} queued"
        else:
            operation, count = _batch_decision(
                kind, request.form.get("action", ""), request.form.getlist("item_id")
            )
            notice = f"{count} proposal(s) decided by {operation}"
        return redirect(url_for("review", lane=lane, document_id=document_id, notice=notice))
    except Exception as exc:  # noqa: BLE001 - denied/invalid writes return to the inbox
        current_app.logger.warning("review decision failed: %s", exc)
        return redirect(url_for("review", lane=lane, document_id=document_id, error=str(exc)))


def register(app, core: ModuleType) -> None:
    """Attach the human-review routes to *app* with late-bound helpers."""
    global _core
    _core = core
    app.add_url_rule("/review", "review", review)
    app.add_url_rule("/review", "review_action", review_action, methods=["POST"])
