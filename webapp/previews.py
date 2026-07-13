"""Document preview-card models shared by the corpus, collection, and document pages."""

from __future__ import annotations

from flask import url_for


def document_preview(doc: dict) -> dict[str, str | None]:
    """Return the webapp preview model for a document row."""
    doc_type = str(doc.get("doc_type") or "file").lower()
    status = str(doc.get("status") or "").lower()
    title = str(doc.get("title") or "document")
    icon = doc_type[:4].upper() if doc_type else "FILE"

    if status in {"failed", "error"}:
        return {
            "kind": "icon",
            "state": "failed",
            "icon": icon,
            "label": f"{title} preview failed",
            "status_label": "Preview failed",
            "src": None,
            "alt": None,
        }
    if status != "extracted":
        return {
            "kind": "icon",
            "state": "pending",
            "icon": icon,
            "label": f"{title} preview pending",
            "status_label": "Preview pending",
            "src": None,
            "alt": None,
        }
    if doc_type == "pdf":
        return {
            "kind": "image",
            "state": "ready",
            "icon": icon,
            "label": f"First-page preview for {title}",
            "status_label": "First-page preview",
            "src": url_for("document_page_image", doc_id=doc["id"], page_number=1),
            "alt": f"First page preview of {title}",
        }
    if doc_type in {"png", "jpg", "jpeg", "gif", "webp", "tif", "tiff", "image"}:
        return {
            "kind": "image",
            "state": "ready",
            "icon": icon,
            "label": f"Image preview for {title}",
            "status_label": "Image preview",
            "src": url_for("document_download", doc_id=doc["id"]),
            "alt": f"Preview of {title}",
        }
    return {
        "kind": "icon",
        "state": "unsupported",
        "icon": icon,
        "label": f"{doc_type.upper()} file preview unavailable",
        "status_label": "Preview unavailable",
        "src": None,
        "alt": None,
    }
