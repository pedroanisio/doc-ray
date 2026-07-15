"""The PDF preview requests the small WebP cover with a 1x/2x srcset.

Regression cover for docs/cover-delivery-optimization.md: the list must point at
`/cover.webp` (a ~15 KB thumbnail), not the full-page render, and offer a 2x
variant so retina cells stay crisp without over-fetching on 1x displays.
"""

from __future__ import annotations

from webapp import app as webapp
from webapp.previews import document_preview

_DOC_ID = "11111111-1111-1111-1111-111111111111"


def _preview(doc_type: str, status: str = "extracted") -> dict:
    with webapp.app.test_request_context("/"):
        return document_preview(
            {"id": _DOC_ID, "doc_type": doc_type, "status": status, "title": "Book"}
        )


def test_pdf_preview_requests_the_webp_cover_with_1x_2x_srcset() -> None:
    preview = _preview("pdf")
    assert preview["kind"] == "image"
    assert "/cover.webp?w=160" in preview["src"]  # 1x for the ~77px cell
    assert "w=160" in preview["srcset"] and "1x" in preview["srcset"]
    assert "w=320" in preview["srcset"] and "2x" in preview["srcset"]  # retina
    assert "pages/1.png" not in preview["src"]  # no longer the full-page render


def test_non_pdf_image_preview_carries_no_srcset() -> None:
    preview = _preview("png")
    assert preview["kind"] == "image"
    assert preview["srcset"] is None


def test_pending_and_failed_previews_stay_icons_without_a_cover_request() -> None:
    for status in ("pending", "failed"):
        preview = _preview("pdf", status=status)
        assert preview["kind"] == "icon"
        assert preview["src"] is None
