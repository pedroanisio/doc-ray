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
        # List cells get a small WebP cover + 1x/2x srcset for the ~77px cell:
        # ~15 KB, not the ~2.3 MB full-page render. The document page's large
        # view keeps the full-page render via ``full_src``. See
        # docs/cover-delivery-optimization.md.
        cover = url_for("document_cover", doc_id=doc["id"], w=160)
        cover_2x = url_for("document_cover", doc_id=doc["id"], w=320)
        return {
            "kind": "image",
            "state": "ready",
            "icon": icon,
            "label": f"First-page preview of {title}",
            "status_label": "First-page cover",
            "src": cover,
            "srcset": f"{cover} 1x, {cover_2x} 2x",
            "full_src": url_for("document_page_image", doc_id=doc["id"], page_number=1),
            "alt": f"First page preview of {title}",
        }
    if doc_type in {"png", "jpg", "jpeg", "gif", "webp", "tif", "tiff", "image"}:
        original = url_for("document_download", doc_id=doc["id"])
        return {
            "kind": "image",
            "state": "ready",
            "icon": icon,
            "label": f"Image preview for {title}",
            "status_label": "Image preview",
            "src": original,
            "srcset": None,
            "full_src": original,
            "alt": f"Preview of {title}",
        }
    return {
        "kind": "icon",
        "state": "unsupported",
        "icon": icon,
        "label": f"{doc_type.upper()} file preview unavailable",
        "status_label": "Preview unavailable",
        "src": None,
        "full_src": None,
        "alt": None,
    }
