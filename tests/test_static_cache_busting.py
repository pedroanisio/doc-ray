"""Static assets are content-hash versioned so deploys never serve stale CSS/JS.

Regression cover for the "broken footer" bug: base.html shipped new ``.footer-*``
markup, but the stylesheet link had no cache-buster, so browsers kept the old
cached design-system.css (which lacked the footer rules) and the footer rendered
unstyled while the rest of the page — older, cache-compatible classes — looked
fine. A ``?v=<content-hash>`` on every static asset ties the cached file to the
HTML that references it: change the bytes, change the URL, force a re-fetch.
"""

from __future__ import annotations

from webapp import app as webapp


def _home() -> str:
    return webapp.app.test_client().get("/").get_data(as_text=True)


def test_stylesheet_link_carries_a_content_version() -> None:
    body = _home()
    assert "design-system.css?v=" in body
    # An unversioned bare reference must not survive — that is the stale-cache bug.
    assert 'design-system.css"' not in body


def test_script_link_carries_a_content_version() -> None:
    body = _home()
    assert "nav-progress.js?v=" in body
    assert 'nav-progress.js"' not in body


def test_asset_version_is_derived_from_file_contents() -> None:
    # Same file -> stable token (cacheable); the token is the content hash prefix.
    with webapp.app.test_request_context("/"):
        first = webapp.static_asset("design-system.css")
        second = webapp.static_asset("design-system.css")
    assert first == second
    assert "?v=" in first
    version = first.split("?v=", 1)[1]
    assert version and version != "0"  # "0" is the file-missing fallback


def test_missing_asset_degrades_to_a_stable_fallback() -> None:
    with webapp.app.test_request_context("/"):
        url = webapp.static_asset("does-not-exist.css")
    assert url.endswith("?v=0")
