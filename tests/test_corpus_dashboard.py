"""Tests for the corpus dashboard generator.

The DB layer is exercised through an in-memory fake that answers each query by
SQL fragment; everything downstream (row assembly, coverage arithmetic, HTML
rendering) is pure and tested directly. The point of separating them is that the
arithmetic a reader will trust — coverage percentages, totals, "missing"
counts — is verified without a database at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import _corpus_dashboard_data as data  # noqa: E402
import _corpus_dashboard_render as render  # noqa: E402


def _doc(doc_id="d1", **kw):
    base = {
        "document_id": doc_id,
        "title": "A Book",
        "doc_type": "epub",
        "status": "extracted",
        "language": "en",
        "page_count": 300,
        "char_count": 500_000,
        "byte_size": 1_200_000,
        "created_at": "2026-07-01T00:00:00+00:00",
    }
    base.update(kw)
    return base


# -- row assembly --------------------------------------------------------


def test_documents_without_artifacts_get_explicit_zeros():
    """A never-processed document must read as 0, not vanish from the report."""
    rows = data.build_rows(
        [_doc()],
        stats={},
        layers={},
        embeddings={},
        images={},
        captions={},
        entity_links={},
        projections={},
        classes={},
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.n_sentences == 0
    assert row.n_tokens == 0
    assert row.embeddings == 0
    assert row.images == 0
    assert row.genre is None
    assert row.layers == frozenset()


def test_row_assembly_joins_every_artifact_family():
    rows = data.build_rows(
        [_doc()],
        stats={"d1": (1200, 34_000, 51_000)},
        layers={"d1": {"ner", "dep", "srl"}},
        embeddings={"d1": {"doc-ray/sbert-mpnet-768": 1200}},
        images={"d1": (42, 40, 9)},
        captions={"d1": {"committed": 30, "review": 5, "rejected": 1}},
        entity_links={"d1": 870},
        projections={"d1": (610, 3)},
        classes={"d1": ("literary-fiction", "novel", "confirmed")},
    )
    row = rows[0]

    assert (row.n_sentences, row.n_tokens, row.n_lexeme_occurrences) == (1200, 34_000, 51_000)
    assert row.embeddings == 1200
    assert row.embedding_models == ("doc-ray/sbert-mpnet-768",)
    assert (row.images, row.image_embeddings, row.vision_sentence_embeddings) == (42, 40, 9)
    assert row.captions_committed == 30
    assert row.captions_pending == 5
    assert row.entity_links == 870
    assert (row.projections, row.lenses) == (610, 3)
    assert row.genre == "literary-fiction"
    assert row.layers == frozenset({"ner", "dep", "srl"})


# -- coverage arithmetic -------------------------------------------------


def test_coverage_counts_documents_holding_each_artifact():
    rows = data.build_rows(
        [_doc("d1"), _doc("d2"), _doc("d3")],
        stats={"d1": (10, 100, 100), "d2": (10, 100, 100)},
        layers={},
        embeddings={"d1": {"m": 10}},
        images={},
        captions={},
        entity_links={},
        projections={},
        classes={},
    )
    cov = {c.label: c for c in data.coverage(rows)}

    assert cov["Sentences"].n_present == 2
    assert cov["Sentences"].pct == pytest.approx(2 / 3 * 100)
    assert cov["Semantic embeddings"].n_present == 1
    assert cov["Semantic embeddings"].n_missing == 2
    assert cov["Images"].n_present == 0
    assert all(c.n_total == 3 for c in cov.values())


def test_coverage_of_an_empty_corpus_does_not_divide_by_zero():
    for entry in data.coverage([]):
        assert entry.pct == 0.0
        assert entry.n_total == 0


def test_totals_sum_every_countable_artifact():
    rows = data.build_rows(
        [_doc("d1"), _doc("d2")],
        stats={"d1": (10, 100, 300), "d2": (5, 50, 150)},
        layers={},
        embeddings={"d1": {"m": 10}, "d2": {"m": 5}},
        images={"d1": (4, 4, 1), "d2": (2, 0, 0)},
        captions={"d1": {"committed": 3, "review": 1}},
        entity_links={"d1": 70, "d2": 30},
        projections={"d1": (60, 2), "d2": (40, 1)},
        classes={},
    )
    t = data.totals(rows, n_distinct_lexemes=999)

    assert t.n_documents == 2
    assert t.n_sentences == 15
    assert t.n_tokens == 150
    assert t.n_embeddings == 15
    assert t.n_images == 6
    assert t.n_captions == 3  # committed only
    assert t.n_entity_links == 100
    assert t.n_distinct_lexemes == 999


# -- fail-closed under RLS ------------------------------------------------


class _Conn:
    """psycopg-shaped double; answers by SQL fragment."""

    def __init__(self, visible: int = 3) -> None:
        self.visible = visible
        self.statements: list[str] = []

    def execute(self, sql, params=None):
        self.statements.append(sql)
        self._last = sql
        return self

    def fetchone(self):
        return (self.visible,)

    def fetchall(self):
        return []

    def close(self):
        return None


def test_an_invisible_corpus_is_refused_rather_than_reported_as_empty(monkeypatch):
    """RLS hides every row from the app role; an empty dashboard would be a lie."""
    monkeypatch.setattr(data.psycopg, "connect", lambda *a, **k: _Conn(visible=0))

    with pytest.raises(RuntimeError, match="no documents visible"):
        data.connect("postgresql://example")


def test_a_visible_corpus_assumes_the_batch_role(monkeypatch):
    monkeypatch.setattr(data.psycopg, "connect", lambda *a, **k: _Conn(visible=637))

    conn = data.connect("postgresql://example")

    assert conn.statements[0] == f"SET ROLE {data.BATCH_ROLE}"


# -- rendering ------------------------------------------------------------


def _report():
    rows = data.build_rows(
        [_doc("d1", title="Nineteen Eighty-Four"), _doc("d2", title="Air France 447")],
        stats={"d1": (8000, 240_000, 300_000)},
        layers={"d1": {"ner"}},
        embeddings={"d1": {"m": 8000}},
        images={"d2": (12, 12, 3)},
        captions={"d2": {"committed": 9}},
        entity_links={"d1": 400},
        projections={},
        classes={},
    )
    return data.Report(
        rows=rows,
        totals=data.totals(rows, n_distinct_lexemes=42),
        coverage=data.coverage(rows),
        generated_at="2026-07-29T17:00:00+00:00",
        schema_version="1.54.0",
        query_seconds=19.4,
    )


def test_render_emits_a_standalone_document_with_both_themes():
    html = render.render(_report())

    assert html.lstrip().startswith("<!doctype html>")
    assert "prefers-color-scheme: dark" in html
    assert '[data-theme="dark"]' in html
    # Self-contained: no external fetches are possible from an operator's laptop.
    assert "http://" not in html.replace("http://www.w3.org", "")
    assert "<script src=" not in html


def test_fragment_omits_the_document_skeleton_but_keeps_theme_and_behaviour():
    """An embedding host supplies doctype/head/body; the theme rules must still bind."""
    body = render.render_body(_report())

    for tag in ("<!doctype", "<html", "<head>", "<body>"):
        assert tag not in body.lower()
    # Theme tokens hang off :root, which the host owns — not a local wrapper.
    assert ":root[data-theme=" in body
    assert "prefers-color-scheme: dark" in body
    assert "<style>" in body and "<script>" in body
    assert "Corpus breakdown" in body


def test_full_document_wraps_the_same_body():
    report = _report()

    assert render.render_body(report) in render.render(report)


def test_render_escapes_titles_rather_than_injecting_them():
    rows = data.build_rows(
        [_doc("d1", title='<script>alert("x")</script>')],
        stats={},
        layers={},
        embeddings={},
        images={},
        captions={},
        entity_links={},
        projections={},
        classes={},
    )
    report = data.Report(
        rows=rows,
        totals=data.totals(rows, 0),
        coverage=data.coverage(rows),
        generated_at="t",
        schema_version="1.54.0",
        query_seconds=0.0,
    )

    html = render.render(report)

    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_render_states_the_denominator_for_every_coverage_bar():
    """A bare percentage invites the reader to invent the denominator."""
    html = render.render(_report())

    assert "of 2 documents" in html
    for entry in _report().coverage:
        assert entry.label in html


def test_render_reports_totals_and_provenance():
    html = render.render(_report())

    assert "1.54.0" in html
    assert "2026-07-29T17:00:00+00:00" in html
    assert "Nineteen Eighty-Four" in html
    assert "Air France 447" in html
