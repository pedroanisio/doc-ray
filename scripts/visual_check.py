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
    python scripts/visual_check.py                       # all pages -> /tmp/docray-visual
    python scripts/visual_check.py --mode corpus
    python scripts/visual_check.py --auth --mode public  # gated (anonymous) nav
    python scripts/visual_check.py --out ./shots
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

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


def _stubbed_app():
    """Import the webapp and swap its DB/GraphQL calls for the test stubs."""
    sys.path.insert(0, str(REPO))
    sys.path.insert(0, str(REPO / "webapp"))
    from tests.test_webapp import fake_q, fake_q1
    from webapp import app as webapp

    def _no_gql(*_args, **_kwargs):
        return {}

    webapp.q = fake_q
    webapp.q1 = fake_q1
    webapp.gql = _no_gql
    webapp.app.config.update(TESTING=True)
    return webapp.app


def main() -> int:
    parser = argparse.ArgumentParser(description="Screenshot the doc-ray webapp.")
    parser.add_argument("--out", default=os.environ.get("DOCRAY_VISUAL_OUT", "/tmp/docray-visual"))
    parser.add_argument("--mode", choices=["public", "corpus", "all"], default="all")
    parser.add_argument("--port", type=int, default=5077)
    parser.add_argument(
        "--auth",
        action="store_true",
        help="enable local auth so the anonymous (gated) nav is exercised",
    )
    args = parser.parse_args()

    if args.auth:
        os.environ["DOCRAY_AUTH_LOCAL_ENABLED"] = "true"

    app = _stubbed_app()

    from werkzeug.serving import make_server

    server = make_server("127.0.0.1", args.port, app)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(1.0)

    shots = []
    if args.mode in ("public", "all"):
        shots += PUBLIC_SHOTS
    if args.mode in ("corpus", "all"):
        shots += CORPUS_SHOTS

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    base = f"http://127.0.0.1:{args.port}"

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for path, viewport, name, open_menu in shots:
            page = browser.new_page(viewport=viewport, device_scale_factor=2)
            page.goto(base + path, wait_until="networkidle")
            if open_menu:
                page.click(".nav-burger")
                page.wait_for_timeout(300)
            page.wait_for_timeout(200)
            page.screenshot(path=str(out / name), full_page=True)
            print(f"shot: {out / name}")
            page.close()
        browser.close()

    server.shutdown()
    print(f"done ({len(shots)} shots -> {out})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
