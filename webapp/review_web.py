"""Corpus-level human review inbox for untrusted machine proposals.

Reads stay RLS-scoped through the webapp database role or GraphQL API. Every
decision crosses GraphQL so the API enforces write scope and records its audit
event; this module never commits a model proposal directly.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import ModuleType
from urllib.parse import quote_plus

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
query ReviewVisual($documentId: ID, $after: String, $limit: Int!, $sort: String!,
                   $minGrounding: Float, $maxGrounding: Float) {
  visualReview(documentId: $documentId, status: "pending", limit: $limit, after: $after,
               sort: $sort, minGrounding: $minGrounding, maxGrounding: $maxGrounding) {
    id documentImageId documentId proposed groundingScore groundingEvidence status
    pageNumber objectKey createdAt cursor
  }
}
"""

#: Sort names the lane offers, in the order they appear in the control.
VISUAL_SORTS = (
    ("grounding_desc", "Best grounded first"),
    ("grounding_asc", "Worst grounded first"),
    ("newest", "Newest first"),
    ("oldest", "Oldest first"),
)

#: Grounding bands an operator can sweep. The floor band exists because
#: rejecting in bulk is the fastest way to shrink a queue of this size.
VISUAL_BANDS = (
    ("", "All bands", None, None),
    ("strong", "Strong (80-100%)", 0.8, None),
    ("moderate", "Moderate (60-80%)", 0.6, 0.8),
    ("weak", "Weak (20-60%)", 0.2, 0.6),
    ("floor", "Floor (0-20%)", None, 0.2),
)
_BANDS_BY_KEY = {key: (lo, hi) for key, _label, lo, hi in VISUAL_BANDS}


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


def _visual_lane(
    document_id: str | None,
    after: str | None,
    *,
    sort: str = "grounding_desc",
    band: str = "",
    page_size: int = PAGE_SIZE,
) -> dict:
    """Return one page of caption proposals, ordered and optionally band-filtered.

    The ordering is the point: returning the queue newest-first put an
    unrepresentative sample of the worst proposals on the opening screen, so an
    operator's first impression of the corpus was wrong (BL-197).
    """
    if sort not in dict(VISUAL_SORTS):
        sort = "grounding_desc"
    low, high = _BANDS_BY_KEY.get(band, (None, None))
    result = _core.gql(
        _VISUAL_QUERY,
        {
            "documentId": document_id,
            "after": after,
            "limit": page_size,
            "sort": sort,
            "minGrounding": low,
            "maxGrounding": high,
        },
    )
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
        "visual_sort": sort,
        "visual_band": band,
        "visual_sorts": VISUAL_SORTS,
        "visual_bands": VISUAL_BANDS,
        "page_size": page_size,
        "next_cursor": items[-1].get("cursor") if len(items) == page_size else None,
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
            model.update(
                _visual_lane(
                    document_id,
                    after,
                    sort=request.args.get("sort", "grounding_desc"),
                    band=request.args.get("band", ""),
                    page_size=max(1, min(200, request.args.get("page_size", PAGE_SIZE, type=int))),
                )
            )
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


def _with(target: str, key: str, value: str) -> str:
    """Append a query parameter to *target*, preserving its fragment and query."""
    base, _, fragment = target.partition("#")
    joiner = "&" if "?" in base else "?"
    suffix = f"#{fragment}" if fragment else ""
    return f"{base}{joiner}{key}={quote_plus(value)}{suffix}"


_RETRACT_MUTATION = """
mutation RetractCaption($captionId: ID!, $reason: String!) {
  retractVisualCaption(captionId: $captionId, reason: $reason)
}
"""


def retract_caption():
    """Retract a published caption the reader can see is wrong (BL-201).

    Captions are committed on write, so this is the recorded human verification
    event: it is attributed, timestamped and keeps the reason. The redirect lands
    back on the image the operator was looking at.
    """
    caption_id = (request.form.get("caption_id") or "").strip()
    document_id = (request.form.get("document_id") or "").strip()
    image_id = (request.form.get("image_id") or "").strip()
    reason = (request.form.get("reason") or "").strip()
    # Keep the reader in the gallery it was looking at. Redirecting to the bare
    # document drops back to the text view, so the operator loses their place
    # after every single retraction — verified by the browser suite, which saw
    # the gallery go from 100 tiles to 0.
    anchor = f"#image-{image_id}" if image_id else ""
    target = f"/doc/{document_id}?view=images{anchor}" if document_id else "/"
    if not caption_id or not reason:
        return redirect(_with(target, "error", "a retraction needs a reason"))
    try:
        data = _core.gql(_RETRACT_MUTATION, {"captionId": caption_id, "reason": reason})
        count = int(data.get("retractVisualCaption") or 0)
    except Exception as exc:  # noqa: BLE001 - a failed write is a message, not a 500
        current_app.logger.warning("caption retraction failed: %s", exc)
        return redirect(_with(target, "error", str(exc)))
    notice = "caption retracted" if count else "caption was not committed; nothing retracted"
    return redirect(_with(target, "notice", notice))


def register(app, core: ModuleType) -> None:
    """Attach the human-review routes to *app* with late-bound helpers."""
    global _core
    _core = core
    app.add_url_rule("/review", "review", review)
    app.add_url_rule("/review", "review_action", review_action, methods=["POST"])
    app.add_url_rule("/captions/retract", "retract_caption", retract_caption, methods=["POST"])
