"""Corpus view modes: table / compact / gallery / grouped, plus sort and density (BL-143).

The corpus page shipped as a single dense table — one 156px row per document, no
way to change layout, sort, density, or grouping. These tests define the view-mode
system: a ``view`` switcher, a whitelisted ``sort``, a ``density`` toggle, and
``group_by`` sectioning, all driven by query params over the same rows and the
same bulk-action selection model.

Like the rest of test_webapp.py, the DB helpers (``q`` / ``q1``) are stubbed with
a dispatcher keyed off the SQL, so these run without Postgres — they exercise the
route logic (param whitelisting, ORDER BY selection, grouping) and the template
(which layout renders, active-state, density/data attributes).
"""

from __future__ import annotations

import pytest

from webapp import app as webapp

# A small, varied corpus: two types (pdf, epub), so grouping-by-type is non-trivial.
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
    },
    {
        "id": "22222222-2222-2222-2222-222222222222",
        "title": "Dune Messiah",
        "doc_type": "pdf",
        "language": "en",
        "status": "extracted",
        "char_count": 344_698,
        "page_count": 257,
        "n_sentences": 4_517,
        "tools": "spacy/en_core_web_trf",
        "created_at": "2026-07-21T10:00:00Z",
    },
    {
        "id": "33333333-3333-3333-3333-333333333333",
        "title": "Making Dreams Reality with AI",
        "doc_type": "epub",
        "language": "en",
        "status": "extracted",
        "char_count": 380_651,
        "page_count": None,
        "n_sentences": 2_842,
        "tools": "spacy/en_core_web_trf",
        "created_at": "2026-07-22T10:00:00Z",
    },
]

_TOTALS = {"docs": 3, "sents": 25_487, "lexemes": 8_000, "occ": 400_000}


@pytest.fixture
def captured():
    """A (client, sql_log) pair: sql_log accumulates every documents-list SQL issued."""
    sql_log: list[str] = []

    def fake_q(sql, params=()):
        norm = " ".join(sql.split())
        if "FROM documents d" in norm and "LIMIT" in norm:
            sql_log.append(norm)
            return [dict(d) for d in _DOCS]
        return []

    def fake_q1(sql, params=()):
        norm = " ".join(sql.split())
        if "FROM documents WHERE lifecycle_state <> 'deleted') docs" in norm:
            return dict(_TOTALS)
        return {}

    orig_q, orig_q1 = webapp.q, webapp.q1
    webapp.q, webapp.q1 = fake_q, fake_q1
    webapp.app.config.update(TESTING=True)
    try:
        yield webapp.app.test_client(), sql_log
    finally:
        webapp.q, webapp.q1 = orig_q, orig_q1


def _html(client, url):
    resp = client.get(url)
    assert resp.status_code == 200, resp.get_data(as_text=True)[:600]
    return resp.get_data(as_text=True)


# --------------------------------------------------------------------------
# View switcher + the four layouts
# --------------------------------------------------------------------------


def test_default_view_is_table_and_backward_compatible(captured):
    client, _ = captured
    html = _html(client, "/corpus")
    assert 'data-view="table"' in html
    assert "<table" in html  # the existing comfortable table is preserved


def test_view_switcher_renders_with_active_state(captured):
    client, _ = captured
    html = _html(client, "/corpus?view=gallery")
    assert 'class="view-switch"' in html
    # every mode is offered
    for mode in ("table", "compact", "gallery", "grouped"):
        assert f"view={mode}" in html
    # the active one is marked for assistive tech
    assert 'aria-current="page"' in html
    assert 'data-view="gallery"' in html


def test_compact_view_renders_the_compact_layout(captured):
    client, _ = captured
    html = _html(client, "/corpus?view=compact")
    assert 'data-view="compact"' in html
    assert 'class="doc-compact"' in html


def test_gallery_view_renders_the_cover_grid(captured):
    client, _ = captured
    html = _html(client, "/corpus?view=gallery")
    assert 'data-view="gallery"' in html
    assert 'class="doc-gallery"' in html


def test_grouped_view_renders_sections(captured):
    client, _ = captured
    html = _html(client, "/corpus?view=grouped")
    assert 'data-view="grouped"' in html
    assert "doc-group" in html


def test_unknown_view_falls_back_to_table(captured):
    client, _ = captured
    html = _html(client, "/corpus?view=../etc/passwd")
    assert 'data-view="table"' in html


# --------------------------------------------------------------------------
# Sort — whitelisted ORDER BY, safe against injection
# --------------------------------------------------------------------------


def test_default_sort_is_by_size(captured):
    client, log = captured
    _html(client, "/corpus")
    assert log and "ORDER BY d.char_count DESC" in log[-1]


def test_sort_title_orders_by_title(captured):
    client, log = captured
    _html(client, "/corpus?sort=title")
    assert "ORDER BY d.title" in log[-1]


def test_sort_recent_orders_by_created_at(captured):
    client, log = captured
    _html(client, "/corpus?sort=recent")
    assert "ORDER BY d.created_at DESC" in log[-1]


def test_sort_sentences_orders_by_sentence_count(captured):
    client, log = captured
    _html(client, "/corpus?sort=sentences")
    assert "ORDER BY n_sentences" in log[-1]


def test_unknown_sort_falls_back_to_size_and_never_reaches_sql(captured):
    client, log = captured
    _html(client, "/corpus?sort=title;DROP TABLE documents;--")
    assert "DROP TABLE" not in log[-1]
    assert "ORDER BY d.char_count DESC" in log[-1]


# --------------------------------------------------------------------------
# Density
# --------------------------------------------------------------------------


def test_density_defaults_to_comfortable(captured):
    client, _ = captured
    html = _html(client, "/corpus")
    assert 'data-density="comfortable"' in html


def test_density_compact_is_honored(captured):
    client, _ = captured
    html = _html(client, "/corpus?density=compact")
    assert 'data-density="compact"' in html


def test_unknown_density_falls_back_to_comfortable(captured):
    client, _ = captured
    html = _html(client, "/corpus?density=huge")
    assert 'data-density="comfortable"' in html


# --------------------------------------------------------------------------
# Group by
# --------------------------------------------------------------------------


def test_group_by_type_sections_the_documents_with_counts(captured):
    client, _ = captured
    html = _html(client, "/corpus?view=grouped&group_by=type")
    # two pdfs, one epub -> a PDF section (2) and an EPUB section (1)
    assert "doc-group" in html
    assert "PDF" in html and "EPUB" in html
    assert "2" in html and "1" in html  # counts surface


def test_sort_and_view_combine(captured):
    client, log = captured
    html = _html(client, "/corpus?view=gallery&sort=title")
    assert 'data-view="gallery"' in html
    assert "ORDER BY d.title" in log[-1]
