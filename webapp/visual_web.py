"""Presentation models for document galleries and text-to-image search."""

from __future__ import annotations

from types import ModuleType

DOCUMENT_VISUAL_QUERY = """
query DocumentVisual($documentId: ID!) {
  documentImages(documentId: $documentId, limit: 100) {
    id documentId pageNumber source widthPx heightPx imageClass cursor
  }
  imageClassSummary(documentId: $documentId) { imageClass count }
  visualCaptions(documentId: $documentId, limit: 100) {
    id documentImageId caption model groundingScore pageNumber
  }
}
"""

IMAGE_SEARCH_QUERY = """
query ImageSearch($query: String!, $limit: Int!) {
  crossModalImageSearch(query: $query, limit: $limit) {
    id documentId pageNumber source imageClass similarity
  }
}
"""


def _coverage(core: ModuleType, document_id: str) -> dict[str, int]:
    row = core.q1(
        """
        SELECT
          (SELECT count(*) FROM document_image WHERE document_id = %s) image_count,
          (SELECT count(*) FROM image_tier1_features WHERE document_id = %s) tier1_count,
          (SELECT count(*) FROM image_embedding WHERE document_id = %s) tier2_count
        """,
        (document_id, document_id, document_id),
    )
    return {
        name: int((row or {}).get(name) or 0)
        for name in ("image_count", "tier1_count", "tier2_count")
    }


def _coverage_state(coverage: dict[str, int]) -> str:
    images = coverage["image_count"]
    tier1 = coverage["tier1_count"]
    if images == 0:
        return "no_images"
    if tier1 == 0:
        return "backfill_not_run"
    if tier1 < images:
        return "partial"
    return "covered"


def document_visual_model(core: ModuleType, document_id: str) -> dict:
    """Return one gallery model with explicit coverage and committed captions."""
    coverage = _coverage(core, document_id)
    try:
        data = core.gql(DOCUMENT_VISUAL_QUERY, {"documentId": document_id})
    except Exception as exc:  # noqa: BLE001 - gallery failure becomes an honest unknown state
        return {
            "images": [],
            "classes": [],
            "coverage": coverage,
            "coverage_state": "unknown",
            "error": str(exc),
        }
    captions = {str(item["documentImageId"]): item for item in data.get("visualCaptions") or []}
    images = []
    for image in data.get("documentImages") or []:
        normalized = dict(image)
        normalized["caption"] = captions.get(str(image["id"]))
        images.append(normalized)
    return {
        "images": images,
        "classes": list(data.get("imageClassSummary") or []),
        "coverage": coverage,
        "coverage_state": _coverage_state(coverage),
        "error": None,
    }


def image_search(core: ModuleType, query: str, *, limit: int) -> list[dict]:
    """Return normalized cross-modal hits enriched with RLS-visible document titles."""
    data = core.gql(IMAGE_SEARCH_QUERY, {"query": query, "limit": limit})
    hits = list(data.get("crossModalImageSearch") or [])
    document_ids = sorted({str(hit["documentId"]) for hit in hits})
    titles = {}
    if document_ids:
        rows = core.q("SELECT id, title FROM documents WHERE id = ANY(%s::uuid[])", (document_ids,))
        titles = {str(row["id"]): str(row["title"]) for row in rows}
    return [
        {
            "image_id": str(hit["id"]),
            "doc_id": str(hit["documentId"]),
            "title": titles.get(str(hit["documentId"]), str(hit["documentId"])),
            "page_number": int(hit["pageNumber"]),
            "source": str(hit["source"]),
            "image_class": hit.get("imageClass"),
            "similarity": float(hit["similarity"]),
        }
        for hit in hits
    ]
