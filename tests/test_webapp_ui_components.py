"""The shared UI layer: icons, component macros, column sort, and row filter.

The browser had no icon vocabulary (five Unicode glyphs across 27 templates), no
component layer to speak of (8 Jinja macros), an ``.empty-state`` class that no
stylesheet rule ever defined, and a corpus table whose columns could not be
sorted by clicking them. These tests define the layer that fixes that, and they
pin the parts that are easy to regress silently:

  * every icon a template references must exist in the sprite (a missing
    ``<symbol>`` renders as nothing at all — no error, no glyph, no test failure
    unless something checks);
  * a sortable header must carry ``aria-sort``, because an arrow communicates
    nothing to a screen reader;
  * the sort direction must round-trip through the whitelist and never reach SQL;
  * the client row filter must describe its own scope, since it can only see one
    page of a paginated corpus.

Like the rest of the webapp suite, ``q``/``q1`` are stubbed off the SQL text, so
these run without Postgres.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from webapp import app as webapp

_TEMPLATES = Path(__file__).resolve().parent.parent / "webapp" / "templates"
_STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"

_DOCS = [
    {
        "id": "11111111-1111-1111-1111-111111111111",
        "title": "Dune",
        "doc_type": "pdf",
        "language": "en",
        "status": "extracted",
        "char_count": 1_157_907,
        "page_count": 416,
        "n_sentences": 18_128,
        "tools": "spacy/en_core_web_trf",
        "created_at": "2026-07-20T10:00:00Z",
    }
]
_TOTALS = {"docs": 1, "sents": 18_128, "lexemes": 8_000, "occ": 400_000}


@pytest.fixture
def captured():
    """A (client, sql_log) pair; sql_log accumulates every documents-list SQL."""
    sql_log: list[str] = []

    def fake_q(sql, params=()):
        norm = " ".join(sql.split())
        if "FROM documents d" in norm and "LIMIT" in norm:
            sql_log.append(norm)
            return [dict(d) for d in _DOCS]
        return []

    def fake_q1(sql, params=()):
        norm = " ".join(sql.split())
        if ") docs," in norm and "sents," in norm:
            return dict(_TOTALS)
        raise AssertionError(f"unexpected q1: {norm}")

    orig_q, orig_q1 = webapp.q, webapp.q1
    webapp.q, webapp.q1 = fake_q, fake_q1
    webapp.app.config.update(TESTING=True)
    try:
        yield webapp.app.test_client(), sql_log
    finally:
        webapp.q, webapp.q1 = orig_q, orig_q1


@pytest.fixture
def empty_corpus():
    """A client whose corpus has no documents, for the empty-state assertions."""

    def fake_q(sql, params=()):
        return []

    def fake_q1(sql, params=()):
        norm = " ".join(sql.split())
        if ") docs," in norm and "sents," in norm:
            return {"docs": 0, "sents": 0, "lexemes": 0, "occ": 0}
        raise AssertionError(f"unexpected q1: {norm}")

    orig_q, orig_q1 = webapp.q, webapp.q1
    webapp.q, webapp.q1 = fake_q, fake_q1
    webapp.app.config.update(TESTING=True)
    try:
        yield webapp.app.test_client()
    finally:
        webapp.q, webapp.q1 = orig_q, orig_q1


def _html(client, url):
    resp = client.get(url)
    assert resp.status_code == 200, resp.get_data(as_text=True)[:600]
    return resp.get_data(as_text=True)


# --------------------------------------------------------------------------
# Icon sprite integrity
# --------------------------------------------------------------------------


def _sprite_symbol_ids() -> set[str]:
    sprite = (_TEMPLATES / "_icons.html").read_text(encoding="utf-8")
    return set(re.findall(r'<symbol id="(i-[a-z0-9-]+)"', sprite))


def _referenced_icon_names() -> set[str]:
    """Every literal name passed to `icons.icon(...)` across the templates.

    Only literals are collected, and the trailing ``[,)]`` is what makes that
    true: a composed name (``'arrow-' ~ dir``) would otherwise be harvested as
    the fragment ``arrow-`` and reported as a missing symbol. Composing an icon
    name is therefore not allowed in a template — name both branches instead —
    and this pattern is what enforces it.
    """
    names: set[str] = set()
    for path in _TEMPLATES.glob("*.html"):
        text = path.read_text(encoding="utf-8")
        names.update(re.findall(r"icons\.icon\(\s*'([a-z0-9-]+)'\s*[,)]", text))
        names.update(re.findall(r'icons\.icon\(\s*"([a-z0-9-]+)"\s*[,)]', text))
    return names


def test_no_template_composes_an_icon_name():
    """A composed name cannot be statically checked against the sprite, so a
    typo in one branch would ship as a silently blank icon."""
    for path in _TEMPLATES.glob("*.html"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"icons\.icon\(\s*['\"][a-z0-9-]*['\"]\s*~", text), path.name


def test_every_referenced_icon_exists_in_the_sprite():
    """A missing <symbol> draws nothing and raises nothing — only a test catches it."""
    missing = {f"i-{n}" for n in _referenced_icon_names()} - _sprite_symbol_ids()
    assert not missing, f"templates reference icons with no <symbol>: {sorted(missing)}"


def test_sprite_carries_no_unused_symbols():
    """The sprite is inlined into every page, so a dead symbol is dead weight on
    every request. Anything defined here must be reachable from a template or
    from the toast module, which builds its marks in JS."""
    js_refs = set(
        re.findall(r'"(i-[a-z0-9-]+)"', (_STATIC / "toast.js").read_text(encoding="utf-8"))
    )
    js_refs |= set(
        re.findall(
            r'"#(i-[a-z0-9-]+)"',
            (_STATIC / "toast.js").read_text(encoding="utf-8"),
        )
    )
    used = {f"i-{n}" for n in _referenced_icon_names()} | js_refs
    assert not _sprite_symbol_ids() - used, (
        f"unused symbols in the sprite: {sorted(_sprite_symbol_ids() - used)}"
    )


def test_icons_are_decorative_unless_they_are_the_only_label(captured):
    """An icon beside its own word must not be announced twice."""
    client, _ = captured
    html = _html(client, "/corpus")
    # The masthead links pair an icon with a word, so those icons are hidden.
    assert '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"' in html
    # The sprite host itself is never announced.
    assert '<svg class="icon-sprite" aria-hidden="true"' in html


def test_navigation_carries_icons_beside_every_destination(captured):
    client, _ = captured
    html = _html(client, "/corpus")
    for name in ("i-rows", "i-layers", "i-search", "i-upload", "i-help-circle"):
        assert f'href="#{name}"' in html, name


# --------------------------------------------------------------------------
# Sortable column headers
# --------------------------------------------------------------------------


def test_table_headers_are_sort_controls_with_aria_sort(captured):
    client, _ = captured
    html = _html(client, "/corpus?view=table&sort=title&dir=asc")
    assert 'class="th-sort th-sort--on"' in html
    assert 'aria-sort="ascending"' in html
    # Unsorted columns declare that they are sortable but not sorted.
    assert 'aria-sort="none"' in html


def test_clicking_a_sorted_column_offers_the_opposite_direction(captured):
    client, _ = captured
    html = _html(client, "/corpus?view=table&sort=title&dir=asc")
    # The active column's own link flips to desc; it must not re-offer asc.
    assert "sort=title&amp;dir=desc" in html


def test_first_click_on_a_magnitude_column_sorts_largest_first(captured):
    client, _ = captured
    html = _html(client, "/corpus?view=table&sort=title&dir=asc")
    assert "sort=sentences&amp;dir=desc" in html


def test_sort_direction_reaches_order_by(captured):
    client, log = captured
    _html(client, "/corpus?sort=title&dir=asc")
    assert "ORDER BY d.title ASC" in log[-1]
    _html(client, "/corpus?sort=title&dir=desc")
    assert "ORDER BY d.title DESC" in log[-1]


def test_sort_direction_defaults_per_column(captured):
    """A title reads A→Z; a magnitude reads largest-first. The default is per
    column, not one global direction."""
    client, log = captured
    _html(client, "/corpus?sort=title")
    assert "ORDER BY d.title ASC" in log[-1]
    _html(client, "/corpus?sort=sentences")
    assert "ORDER BY n_sentences DESC" in log[-1]


def test_hostile_direction_never_reaches_sql(captured):
    client, log = captured
    _html(client, "/corpus?sort=title&dir=ASC;DROP TABLE documents;--")
    assert "DROP TABLE" not in log[-1]
    assert "ORDER BY d.title ASC" in log[-1]


def test_sort_is_stable_across_pages(captured):
    """Every ordering ends in a unique tiebreaker, or a row can appear on two
    pages (or on neither) when the sort key ties."""
    client, log = captured
    for key in ("size", "recent", "title", "sentences", "pages"):
        _html(client, f"/corpus?sort={key}")
        assert log[-1].split("ORDER BY")[1].strip().startswith(("d.", "n_sentences")), log[-1]
        assert ", d.id" in log[-1].split("ORDER BY")[1], f"{key} has no tiebreaker"


def test_sort_survives_a_view_change(captured):
    client, _ = captured
    html = _html(client, "/corpus?view=table&sort=title&dir=asc")
    assert "view=compact" in html and "sort=title" in html
    assert "dir=asc" in html


# --------------------------------------------------------------------------
# Client-side row filter
# --------------------------------------------------------------------------


def test_row_filter_is_offered_and_declares_its_scope(captured):
    """The filter can only see the rows on this page, so it must not present
    itself as a corpus search."""
    client, _ = captured
    html = _html(client, "/corpus?view=table")
    assert 'class="table-filter"' in html
    assert "data-row-filter" in html
    assert "this page" in html.lower()


def test_row_filter_is_absent_when_there_are_no_rows(empty_corpus):
    html = _html(empty_corpus, "/corpus")
    assert "data-row-filter" not in html


# --------------------------------------------------------------------------
# Empty states
# --------------------------------------------------------------------------


def test_empty_corpus_offers_the_action_that_fills_it(empty_corpus):
    html = _html(empty_corpus, "/corpus")
    assert 'class="empty-state"' in html
    assert "empty-state__title" in html
    assert "/upload" in html


def test_empty_state_class_is_actually_styled():
    """`.empty-state` was used by a template and defined by no rule, so it
    rendered as unstyled body text. Keep the class and its rule together."""
    css = (_STATIC / "design-system.css").read_text(encoding="utf-8")
    assert ".empty-state {" in css
    assert ".empty-state__title" in css


# --------------------------------------------------------------------------
# Toast live region
# --------------------------------------------------------------------------


def test_toast_region_is_declared_before_anything_is_posted_to_it(captured):
    """A live region created at the same moment as its text is frequently not
    announced; it has to already be in the accessibility tree."""
    client, _ = captured
    html = _html(client, "/corpus")
    assert 'id="toast-region"' in html
    assert 'aria-live="polite"' in html
    assert 'role="status"' in html


def test_toast_region_is_empty_in_the_served_markup(captured):
    client, _ = captured
    html = _html(client, "/corpus")
    assert '<div class="toast-region" id="toast-region" role="status" ' in html
    region = html.split('id="toast-region"')[1]
    assert region.lstrip().startswith('role="status" aria-live="polite" aria-atomic="false"></div>')


def test_error_toasts_do_not_auto_dismiss():
    """Timing out a failure message is the one case where the pattern loses
    information the user needed."""
    js = (_STATIC / "toast.js").read_text(encoding="utf-8")
    assert 'if (kind !== "error")' in js


def test_toast_text_is_never_treated_as_markup():
    """A toast routinely carries a filename or a raw server error string."""
    js = (_STATIC / "toast.js").read_text(encoding="utf-8")
    assert not re.search(r"\.innerHTML\s*=", js)
    assert "textContent" in js
