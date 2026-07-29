"""One-handed reach: section switching and Back must sit where the thumb is.

On a phone the constraint that dominates layout is reach, not screen area.
Roughly half of users operate the device one-handed and the reachable region is
the bottom of the screen, so the primary destinations belong in a bottom bar and
the back affordance belongs in the lower region — not in a masthead that expands
downward from the top edge, which is the hardest place a thumb can go.

These tests read the rendered browser chrome and the stylesheet's coarse-pointer
cascade. Sizing of the bar's own targets is enforced separately by
``scripts/check_touch_targets.py``.
"""

from __future__ import annotations

import check_touch_targets as touch
import pytest
from flask import render_template_string

from webapp import app as webapp
from webapp import auth_web

_STYLESHEET = touch.DEFAULT_STYLESHEET


@pytest.fixture
def client():
    webapp.app.config.update(TESTING=True)
    return webapp.app.test_client()


def _coarse_rules():
    """Return the stylesheet rules that apply once the pointer is coarse."""
    rules, tokens = touch._load(None)
    return [rule for rule in rules if touch._applies_on_touch(rule.media)], tokens


def _declaration(selector: str, prop: str) -> str | None:
    """Return the last coarse-context value of *prop* for *selector*."""
    rules, _ = _coarse_rules()
    found = None
    for rule in rules:
        if selector in rule.selectors:
            for name, value in rule.declarations:
                if name == prop:
                    found = value
    return found


# --- the bar itself ---------------------------------------------------------


def test_the_browser_renders_a_bottom_navigation_bar(client):
    body = client.get("/help").get_data(as_text=True)
    assert '<nav class="bottom-nav"' in body
    assert 'aria-label="Primary"' in body


def test_the_bar_carries_the_most_frequent_destinations(client):
    """Corpus, Search and Upload are the recurring tasks; they get the slots."""
    body = client.get("/help").get_data(as_text=True)
    bar = body.split('<nav class="bottom-nav"', 1)[1].split("</nav>", 1)[0]
    for href in ("/corpus", "/search", "/upload"):
        assert f'href="{href}"' in bar, f"{href} missing from the bottom bar"


def test_the_bar_reaches_the_rest_of_the_navigation(client):
    """The long tail stays one tap away, opened from the thumb region."""
    body = client.get("/help").get_data(as_text=True)
    bar = body.split('<nav class="bottom-nav"', 1)[1].split("</nav>", 1)[0]
    assert 'for="nav-open"' in bar


def test_the_bar_is_absent_when_the_app_navigation_is(client, monkeypatch):
    """An anonymous visitor on a gated instance has nowhere to switch to."""
    monkeypatch.setattr(auth_web, "web_auth_required", lambda: True)
    body = client.get("/login").get_data(as_text=True)
    assert "bottom-nav" not in body


def test_the_bar_is_anchored_to_the_bottom_of_the_viewport():
    assert _declaration(".bottom-nav", "position") == "fixed"
    assert _declaration(".bottom-nav", "bottom") == "0"


def test_the_bar_clears_the_device_safe_area():
    """A home indicator must not sit on top of a tap target."""
    padding = _declaration(".bottom-nav", "padding-bottom") or ""
    assert "safe-area-inset-bottom" in padding


def test_content_reserves_room_so_the_bar_never_covers_it():
    reserved = _declaration("main", "padding-bottom") or ""
    assert "--bottom-nav-h" in reserved


def test_the_bar_is_hidden_where_the_pointer_is_fine():
    """A mouse does not need a thumb bar; the masthead nav is already correct."""
    text = _STYLESHEET.read_text(encoding="utf-8")
    base = text.split("@media (pointer: coarse)", 1)[0]
    assert ".bottom-nav {" in base
    assert "display: none" in base.split(".bottom-nav {", 1)[1].split("}", 1)[0]


# --- the expanded menu ------------------------------------------------------


def test_the_expanded_menu_opens_into_the_lower_region_on_touch():
    """Opening the menu from a bottom bar must not send the thumb to the top."""
    assert _declaration("header .nav-cb:checked ~ nav", "position") == "fixed"
    bottom = _declaration("header .nav-cb:checked ~ nav", "bottom") or ""
    assert "--bottom-nav-h" in bottom


def test_the_expanded_menu_can_scroll_when_it_is_taller_than_its_region():
    assert _declaration("header .nav-cb:checked ~ nav", "overflow-y") == "auto"


# --- the back affordance ----------------------------------------------------


def test_a_page_with_a_parent_offers_back_in_the_lower_region():
    with webapp.app.test_request_context("/"):
        html = render_template_string(
            "{% extends 'base.html' %}"
            "{% set back_url = '/corpus' %}{% set back_label = 'Corpus' %}"
            "{% block body %}{% endblock %}"
        )
    bar = html.split('<nav class="bottom-nav"', 1)[1].split("</nav>", 1)[0]
    assert 'class="bottom-nav__back"' in bar
    assert 'href="/corpus"' in bar
    assert "Corpus" in bar


def test_a_page_without_a_parent_leaves_the_back_slot_empty():
    with webapp.app.test_request_context("/"):
        html = render_template_string("{% extends 'base.html' %}{% block body %}{% endblock %}")
    bar = html.split('<nav class="bottom-nav"', 1)[1].split("</nav>", 1)[0]
    assert "bottom-nav__back" not in bar


@pytest.mark.parametrize(
    "template",
    [
        "collection.html",
        "collections.html",
        "document.html",
        "lexeme.html",
        "sharing.html",
        "seeding.html",
        "operator_metadata.html",
    ],
)
def test_every_child_page_declares_its_parent(template):
    """A page that shows a top back-link must offer the same target below."""
    text = (touch._ROOT / "webapp" / "templates" / template).read_text(encoding="utf-8")
    assert "{% set back_url" in text, f"{template} has no lower-region back target"
    assert "{% set back_label" in text


# --- sizing -----------------------------------------------------------------


def test_the_bars_own_targets_meet_the_platform_minimum():
    assert ".bottom-nav a" in touch.CONTROLS
    assert touch.find_violations(controls=(".bottom-nav a",)) == []
