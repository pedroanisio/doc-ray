"""Regression tests for the developer visual-check harness."""

from __future__ import annotations

from scripts import visual_check


def test_webapp_epic_visual_matrix_covers_every_new_human_surface():
    """The screenshot matrix must exercise every route introduced by epic #23."""
    paths = {shot[0] for shot in visual_check.WEBAPP_EPIC_SHOTS}

    assert {
        "/search?mode=semantic&q=how+the+story+ends",
        "/search?mode=image&q=rising+revenue+chart",
        "/review?lane=metadata&document_id=11111111-1111-1111-1111-111111111111",
        "/review?lane=visual",
        "/briefing?topic=general+relativity",
        "/collections",
        "/doc/11111111-1111-1111-1111-111111111111?view=images",
    } <= paths
    for path in paths:
        for viewport in (visual_check.DESKTOP, visual_check.MOBILE):
            assert any(
                shot[0] == path and shot[1] == viewport for shot in visual_check.WEBAPP_EPIC_SHOTS
            )


def test_stubbed_visual_app_renders_meaningful_epic_states():
    """The harness must render populated states, not empty or unavailable fallbacks."""
    expected = {
        "/search?mode=semantic&q=how+the+story+ends": "87.6%",
        "/search?mode=image&q=rising+revenue+chart": "91.2%",
        "/review?lane=metadata&document_id=11111111-1111-1111-1111-111111111111": (
            "Nineteen Eighty-Four"
        ),
        "/review?lane=visual": "A red line rises across the chart.",
        "/briefing?topic=general+relativity": "Gravity is curvature.",
        "/collections": "Research lens",
        "/doc/11111111-1111-1111-1111-111111111111?view=images": (
            "Revenue rises from left to right."
        ),
    }

    with visual_check._stubbed_app() as app:
        client = app.test_client()
        for path, marker in expected.items():
            response = client.get(path)
            assert response.status_code == 200, path
            assert marker in response.get_data(as_text=True), path


def test_visual_harness_stubs_reader_previews_as_well_as_gallery_images():
    """A visual proof must not contain broken images from the absent API container."""
    assert "**/doc/*/cover.webp*" in visual_check.PLACEHOLDER_ROUTE_PATTERNS
    assert "**/doc/*/image/*.webp*" in visual_check.PLACEHOLDER_ROUTE_PATTERNS
    assert "**/doc/*/page/*.png*" in visual_check.PLACEHOLDER_ROUTE_PATTERNS


def test_stubbed_visual_app_restores_live_bindings_after_use():
    """Regression: screenshot fixtures must never leak into later webapp tests."""
    from webapp import app as webapp

    originals = (webapp.q, webapp.q1, webapp.gql)
    with visual_check._stubbed_app() as app:
        assert app.test_client().get("/briefing?topic=gravity").status_code == 200

    assert (webapp.q, webapp.q1, webapp.gql) == originals
