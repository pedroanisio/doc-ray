"""Render the doc-ray webapp with the test DB stubs and screenshot key pages.

A developer visual-check harness. It serves the real Flask app (real static CSS
and Google Fonts) backed by the same ``fake_q`` / ``fake_q1`` dispatchers the
unit tests use, so no Postgres is required, then drives Chromium (Playwright) at
desktop and phone viewports. Use it to eyeball design/layout/regressions that a
green test suite would not surface.

Setup (once):
    uv pip install -r requirements-dev.txt   # installs playwright
    playwright install chromium              # downloads the browser

Run:
    python scripts/visual_check.py                       # all pages -> $DOCRAY_WORK_DIR/scratch/
    python scripts/visual_check.py --mode corpus
    python scripts/visual_check.py --mode epic           # epic #23 human surfaces
    python scripts/visual_check.py --auth --mode public  # gated (anonymous) nav
    python scripts/visual_check.py --out ./shots
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _work_dir import work_dir  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

DESKTOP = {"width": 1280, "height": 900}
MOBILE = {"width": 390, "height": 844}

# (path, viewport, filename, open_mobile_menu)
PUBLIC_SHOTS = [
    ("/", DESKTOP, "landing_desktop.png", False),
    ("/", MOBILE, "landing_mobile.png", False),
    ("/login", DESKTOP, "login_desktop.png", False),
    ("/login", MOBILE, "login_mobile.png", False),
    ("/", MOBILE, "landing_menu_open.png", True),
]
CORPUS_SHOTS = [
    ("/corpus", DESKTOP, "corpus_desktop.png", False),
    ("/corpus", MOBILE, "corpus_mobile.png", False),
]
ERROR_SHOTS = [
    ("/no-such-page", DESKTOP, "error_404_desktop.png", False),
    ("/no-such-page", MOBILE, "error_404_mobile.png", False),
]
DOC_ID = "11111111-1111-1111-1111-111111111111"
WEBAPP_EPIC_PATHS = [
    ("/search?mode=semantic&q=how+the+story+ends", "search_semantic"),
    ("/search?mode=image&q=rising+revenue+chart", "search_images"),
    (f"/review?lane=metadata&document_id={DOC_ID}", "review_metadata"),
    ("/review?lane=visual", "review_visual"),
    ("/briefing?topic=general+relativity", "briefing"),
    ("/collections", "collections_lenses"),
    (f"/doc/{DOC_ID}?view=images", "document_images"),
]
WEBAPP_EPIC_SHOTS = [
    (path, viewport, f"{name}_{size}.png", False)
    for path, name in WEBAPP_EPIC_PATHS
    for viewport, size in ((DESKTOP, "desktop"), (MOBILE, "mobile"))
]
PLACEHOLDER_ROUTE_PATTERNS = (
    "**/doc/*/cover.webp*",
    "**/doc/*/image/*.webp*",
    "**/doc/*/page/*.png*",
)


def _visual_q(fake_q, sql, params=()):
    """Return populated SQL states for the screenshot-only epic routes."""
    normalized = " ".join(str(sql).split())
    if "FROM document_metadata_suggestion s" in normalized:
        return [{"id": DOC_ID, "title": "Nineteen Eighty-Four", "pending_count": 2}]
    if "FROM document_collections c" in normalized and "LEFT JOIN" in normalized:
        return [
            {
                "id": "collection-1",
                "name": "Primary research",
                "description": "Sources selected for the current investigation.",
                "kind": "manual",
                "rule": {},
                "is_scope": True,
                "parent_lens_id": None,
                "parent_lens_name": None,
                "document_count": 1,
            }
        ]
    if "SELECT id, title FROM documents WHERE id = ANY" in normalized:
        return [{"id": DOC_ID, "title": "Nineteen Eighty-Four"}]
    return fake_q(sql, params)


def _visual_q1(fake_q1, sql, params=()):
    """Return queue and visual-coverage counters for screenshot states."""
    normalized = " ".join(str(sql).split())
    if "document_metadata_suggestion" in normalized and "visual_review" in normalized:
        return {"metadata": 2, "seeds": 1, "resolutions": 1, "visual": 1}
    if "FROM document_image" in normalized and "tier1_count" in normalized:
        return {"image_count": 1, "tier1_count": 1, "tier2_count": 1}
    return fake_q1(sql, params)


def _visual_gql(query, variables=None):
    """Return representative GraphQL payloads for each epic screenshot."""
    if "semanticSearchSentences" in query:
        return {
            "semanticSearchSentences": [
                {
                    "sentenceId": "sentence-1",
                    "documentId": DOC_ID,
                    "ordinal": 73,
                    "text": "At last he accepted the world as it was.",
                    "similarity": 0.8764,
                }
            ]
        }
    if "crossModalImageSearch" in query:
        return {
            "crossModalImageSearch": [
                {
                    "id": "image-1",
                    "documentId": DOC_ID,
                    "pageNumber": 7,
                    "source": "pdf-xobject",
                    "imageClass": "chart",
                    "similarity": 0.912,
                }
            ]
        }
    if "documentMetadataSuggestions" in query:
        return {
            "documentMetadataSuggestions": [
                {
                    "id": "meta-1",
                    "documentId": DOC_ID,
                    "field": "title",
                    "currentValue": "orwell1984",
                    "suggestedValue": "Nineteen Eighty-Four",
                    "confidence": 0.93,
                    "confidenceBand": "high",
                    "reason": "Title-page evidence matches the publication record.",
                    "source": "metadata-verifier",
                }
            ]
        }
    if "visualReview" in query:
        return {
            "visualReview": [
                {
                    "id": "visual-1",
                    "documentImageId": "image-1",
                    "documentId": DOC_ID,
                    "proposed": {"caption": "A red line rises across the chart."},
                    "groundingScore": 0.84,
                    "groundingEvidence": {"ocr": "Revenue"},
                    "pageNumber": 7,
                    "objectKey": "images/chart.png",
                    "cursor": "next-visual",
                }
            ]
        }
    if "briefingPack" in query:
        return {
            "briefingPack": {
                "topic": "general relativity",
                "retrieval": "full-text and semantic evidence retrieval",
                "sources": [
                    {
                        "documentId": DOC_ID,
                        "title": "Field Equations",
                        "rank": 0.9,
                        "cite": {
                            "documentId": DOC_ID,
                            "sentenceOrdinal": None,
                            "ref": "Field Equations",
                        },
                    }
                ],
                "keyPassages": [
                    {
                        "documentId": DOC_ID,
                        "title": "Field Equations",
                        "sentenceOrdinal": 73,
                        "text": "Gravity is curvature.",
                        "matchedTerms": ["general relativity", "relativity"],
                        "cite": {
                            "documentId": DOC_ID,
                            "sentenceOrdinal": 73,
                            "ref": "Field Equations · sentence 73",
                        },
                    }
                ],
                "entities": [{"text": "Albert Einstein", "entityType": "PERSON", "count": 8}],
                "coverage": {"documents": 1, "passages": 1},
                "gaps": ["One lower-ranked passage did not fit the response budget."],
                "guidance": "Confirm each citation before synthesis.",
            }
        }
    if "documentImages" in query:
        return {
            "documentImages": [
                {
                    "id": "image-1",
                    "documentId": DOC_ID,
                    "pageNumber": 7,
                    "source": "pdf-xobject",
                    "widthPx": 1200,
                    "heightPx": 800,
                    "imageClass": "chart",
                    "cursor": "next-image",
                }
            ],
            "imageClassSummary": [{"imageClass": "chart", "count": 1}],
            "visualCaptions": [
                {
                    "id": "caption-1",
                    "documentImageId": "image-1",
                    "caption": "Revenue rises from left to right.",
                    "model": "trusted-vlm",
                    "groundingScore": 0.94,
                    "pageNumber": 7,
                }
            ],
        }
    if "lenses" in query:
        return {
            "lenses": [
                {
                    "id": "collection-1",
                    "name": "Research lens",
                    "description": "Vocabulary for a future projection scope.",
                    "isScope": True,
                    "parentLensId": None,
                    "documentCount": 1,
                    "cursor": "next-lens",
                }
            ]
        }
    return {}


@contextmanager
def _stubbed_app():
    """Yield the webapp with screenshot stubs, then restore its live bindings."""
    sys.path.insert(0, str(REPO))
    sys.path.insert(0, str(REPO / "webapp"))
    from tests.test_webapp import fake_q, fake_q1
    from webapp import app as webapp

    original_bindings = (webapp.q, webapp.q1, webapp.gql)
    original_testing = webapp.app.config.get("TESTING")
    try:
        webapp.q = lambda sql, params=(): _visual_q(fake_q, sql, params)
        webapp.q1 = lambda sql, params=(): _visual_q1(fake_q1, sql, params)
        webapp.gql = _visual_gql
        webapp.app.config.update(TESTING=True)
        yield webapp.app
    finally:
        webapp.q, webapp.q1, webapp.gql = original_bindings
        webapp.app.config.update(TESTING=original_testing)


def _capture_screenshots(args, app) -> int:
    """Serve *app* and capture the screenshot matrix selected by *args*."""
    from werkzeug.serving import make_server

    server = make_server("127.0.0.1", args.port, app)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(1.0)

    shots = []
    if args.mode in ("public", "all"):
        shots += PUBLIC_SHOTS
    if args.mode in ("corpus", "all"):
        shots += CORPUS_SHOTS
    if args.mode in ("epic", "all"):
        shots += WEBAPP_EPIC_SHOTS
    if args.mode in ("error", "all"):
        shots += ERROR_SHOTS

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    base = f"http://127.0.0.1:{args.port}"

    from playwright.sync_api import sync_playwright

    color_scheme = "dark" if args.dark else "light"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            placeholder = REPO / "webapp" / "static" / "icon-512.png"
            for path, viewport, name, open_menu in shots:
                page = browser.new_page(
                    viewport=viewport, device_scale_factor=2, color_scheme=color_scheme
                )
                for pattern in PLACEHOLDER_ROUTE_PATTERNS:
                    page.route(
                        pattern,
                        lambda route: route.fulfill(path=placeholder, content_type="image/png"),
                    )
                page.goto(base + path, wait_until="networkidle")
                if open_menu:
                    page.click(".nav-burger")
                    page.wait_for_timeout(300)
                page.wait_for_timeout(200)
                filename = name.replace(".png", "_dark.png") if args.dark else name
                page.screenshot(path=str(out / filename), full_page=True)
                print(f"shot: {out / filename}")
                page.close()
            browser.close()
    finally:
        server.shutdown()

    print(f"done ({len(shots)} shots -> {out})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Screenshot the doc-ray webapp.")
    # Screenshots are scratch output and go to the local working store, never
    # into the tree.
    parser.add_argument(
        "--out",
        default=os.environ.get("DOCRAY_VISUAL_OUT") or str(work_dir("scratch", "visual")),
    )
    parser.add_argument(
        "--mode",
        choices=["public", "corpus", "epic", "error", "all"],
        default="all",
    )
    parser.add_argument("--port", type=int, default=5077)
    parser.add_argument(
        "--auth",
        action="store_true",
        help="enable local auth so the anonymous (gated) nav is exercised",
    )
    parser.add_argument(
        "--dark",
        action="store_true",
        help="render in dark mode (prefers-color-scheme: dark) and suffix files _dark",
    )
    args = parser.parse_args()

    if args.auth:
        os.environ["DOCRAY_AUTH_LOCAL_ENABLED"] = "true"

    with _stubbed_app() as app:
        return _capture_screenshots(args, app)


if __name__ == "__main__":
    raise SystemExit(main())
