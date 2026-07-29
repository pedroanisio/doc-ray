"""Browser-driven guards for the caption review queue (BL-194/197/198/199).

These drive the real rendered page, not the template source: the complaint that
started this work was that the queue could not be used, and a template diff
cannot demonstrate that it now can.

The suite skips unless ``DOCRAY_UI_BASE_URL`` and ``DOCRAY_UI_SESSION`` are set,
because the review surface is behind the auth wall — it is an operator-run check
against a live stack, not a unit test.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import Page, expect, sync_playwright  # noqa: E402

_BASE = os.environ.get("DOCRAY_UI_BASE_URL", "")
_SESSION = os.environ.get("DOCRAY_UI_SESSION", "")

pytestmark = pytest.mark.skipif(
    not (_BASE and _SESSION),
    reason="set DOCRAY_UI_BASE_URL and DOCRAY_UI_SESSION to drive the live review UI",
)


@pytest.fixture(scope="module")
def page():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        host = _BASE.split("://", 1)[-1].split(":", 1)[0]
        context.add_cookies([{"name": "session", "value": _SESSION, "domain": host, "path": "/"}])
        pg = context.new_page()
        yield pg
        browser.close()


def _open_queue(pg: Page, **params: str) -> None:
    query = "&".join(f"{k}={v}" for k, v in {"lane": "visual", **params}.items())
    pg.goto(f"{_BASE}/review?{query}", wait_until="domcontentloaded")


def test_the_queue_renders_proposals(page: Page) -> None:
    _open_queue(page)
    expect(page.get_by_test_id("proposal").first).to_be_visible()


def test_triage_controls_are_present(page: Page) -> None:
    """Sort, band and page size — the three the operator had none of."""
    _open_queue(page)
    for control in ("sort-select", "band-select", "page-size-select"):
        expect(page.get_by_test_id(control)).to_be_visible()


def test_the_default_order_puts_best_grounded_first(page: Page) -> None:
    """The opening screen must not be an unrepresentative sample of the worst."""
    _open_queue(page)
    scores = [int(t.split("%")[0]) for t in page.get_by_test_id("grounding-pill").all_inner_texts()]
    assert scores == sorted(scores, reverse=True), f"not descending: {scores[:8]}"


def test_sorting_ascending_reverses_the_order(page: Page) -> None:
    _open_queue(page, sort="grounding_asc")
    scores = [int(t.split("%")[0]) for t in page.get_by_test_id("grounding-pill").all_inner_texts()]
    assert scores == sorted(scores), f"not ascending: {scores[:8]}"


def test_the_band_filter_bounds_the_scores_shown(page: Page) -> None:
    _open_queue(page, band="floor")
    scores = [int(t.split("%")[0]) for t in page.get_by_test_id("grounding-pill").all_inner_texts()]
    assert scores, "the floor band should not be empty on this corpus"
    assert max(scores) <= 20, f"floor band leaked a higher score: {max(scores)}"


def test_page_size_is_honoured(page: Page) -> None:
    """25 per page over 5,700 items is 230 pages; the size must be raisable."""
    _open_queue(page, page_size="100")
    assert page.get_by_test_id("proposal").count() == 100


def test_select_all_selects_every_row_on_the_page(page: Page) -> None:
    _open_queue(page, page_size="25")
    page.get_by_test_id("select-all").check()
    boxes = page.get_by_test_id("proposal-checkbox")
    assert boxes.count() == 25
    for i in range(boxes.count()):
        assert boxes.nth(i).is_checked()


def test_the_selected_count_is_shown_before_the_action_is_taken(page: Page) -> None:
    """A destructive bulk action must state how many rows it affects."""
    _open_queue(page, page_size="25")
    page.get_by_test_id("select-all").check()
    expect(page.get_by_test_id("selected-count")).to_have_text("25 selected")


def test_actions_are_disabled_until_something_is_selected(page: Page) -> None:
    _open_queue(page)
    expect(page.get_by_test_id("approve-btn")).to_be_disabled()
    page.get_by_test_id("proposal-checkbox").first.check()
    expect(page.get_by_test_id("approve-btn")).to_be_enabled()


def test_each_proposal_shows_the_image_it_describes(page: Page) -> None:
    """The decision is 'is this true of that image', so the image must be present."""
    _open_queue(page)
    expect(page.get_by_test_id("proposal-thumb").first).to_be_visible()


def test_the_queue_discloses_its_scale_not_just_a_count(page: Page) -> None:
    _open_queue(page)
    progress = page.get_by_test_id("queue-progress").inner_text()
    # Either the scale is disclosed, or the total is honestly marked unavailable.
    # What must never appear is an unverified total presented as fact.
    assert ("pages at this size" in progress) or ("unavailable" in progress), progress
    assert "of 0 pending" not in progress, progress


def test_captions_are_clamped_so_proposals_can_be_compared_by_scanning(page: Page) -> None:
    """Unclamped walls of text made adjacent proposals incomparable (BL-198)."""
    _open_queue(page)
    heights = page.get_by_test_id("caption-text").evaluate_all(
        "els => els.map(e => e.getBoundingClientRect().height)"
    )
    assert heights, "no captions rendered"
    assert max(heights) <= 200, f"a caption rendered {max(heights)}px tall"
