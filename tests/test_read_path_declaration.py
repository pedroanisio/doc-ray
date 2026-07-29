"""The corpus band must say what each of its numbers promises, on every surface.

Four totals sit side by side and three read paths sit under them: an exact live
count, two pipeline rollups, and one debounced scalar that may legitimately lag.
Printing them identically turns the debounced one into a failure that looks like
a success — well-formed, plausible, wrong, and detectable only by the user who
just wrote.

A human reads the guarantee off the band; an agent reads it as data. Both must
get the same answer, so the browser, the GraphQL field and the MCP tool are
tested against one domain declaration.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from doc_ray.domain.read_consistency import CORPUS_STAT_GUARANTEES, guarantee_for
from tests.test_webapp import _norm, fake_q, fake_q1
from webapp import app as webapp


def _ago(**delta: float) -> datetime:
    """A timestamp *delta* before **now**, evaluated at call time.

    Freshness is measured against the wall clock when the row is read, so a
    module-level ``_NOW`` silently ages as the suite runs: the same row that
    reads as 120 s old in an isolated run reads as 133 s old behind a full
    suite, and the age assertion fails on suite duration rather than on
    behaviour. Recomputing per call keeps the offset exact.
    """
    return datetime.now(timezone.utc) - timedelta(**delta)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(webapp, "q", fake_q)
    monkeypatch.setattr(webapp, "q1", fake_q1)
    webapp.app.config.update(TESTING=True)
    return webapp.app.test_client()


def _totals(monkeypatch, **overrides):
    """Point the corpus totals query at a chosen row."""
    row = {
        "docs": 5,
        "sents": 15200,
        "lexemes": 30013,
        "occ": 283809,
        "lexemes_computed_at": _ago(minutes=2),
    }
    row.update(overrides)

    def _q1(sql, params=()):
        text = _norm(sql)
        if ") docs," in text and "sents," in text:
            return row
        return fake_q1(sql, params)

    monkeypatch.setattr(webapp, "q1", _q1)


# --- the browser ------------------------------------------------------------


def test_the_band_reads_the_rollup_timestamp(client, monkeypatch):
    """The declaration is worthless without the value it is asserted about."""
    captured: list[str] = []

    def _q1(sql, params=()):
        captured.append(_norm(sql))
        return fake_q1(sql, params)

    monkeypatch.setattr(webapp, "q1", _q1)
    client.get("/corpus")

    totals = next((q for q in captured if ") docs," in q and "sents," in q), None)
    assert totals is not None
    assert "computed_at" in totals


def test_every_figure_carries_its_guarantee(client, monkeypatch):
    _totals(monkeypatch)
    html = client.get("/corpus").get_data(as_text=True)
    for declaration in CORPUS_STAT_GUARANTEES:
        assert f'data-guarantee="{declaration.guarantee.value}"' in html
        assert f'data-figure="{declaration.figure}"' in html


def test_the_debounced_figure_shows_when_it_was_computed(client, monkeypatch):
    _totals(monkeypatch, lexemes_computed_at=_ago(minutes=2))
    html = client.get("/corpus").get_data(as_text=True)
    assert "computed 2 minutes ago" in html


def test_a_figure_past_its_window_is_marked_stale(client, monkeypatch):
    _totals(monkeypatch, lexemes_computed_at=_ago(minutes=45))
    html = client.get("/corpus").get_data(as_text=True)
    assert 'data-stale="true"' in html
    assert "computed 45 minutes ago" in html


def test_a_figure_inside_its_window_is_not_marked_stale(client, monkeypatch):
    _totals(monkeypatch, lexemes_computed_at=_ago(minutes=3))
    html = client.get("/corpus").get_data(as_text=True)
    assert 'data-stale="true"' not in html


def test_a_never_computed_rollup_says_so(client, monkeypatch):
    _totals(monkeypatch, lexemes_computed_at=None)
    html = client.get("/corpus").get_data(as_text=True)
    assert "never computed" in html


def test_the_exact_count_is_not_dressed_up_as_a_rollup(client, monkeypatch):
    _totals(monkeypatch)
    html = client.get("/corpus").get_data(as_text=True)
    band = html.split('class="stat-rail"', 1)[1].split("</p>", 1)[0]
    documents = band.split('data-figure="documents"', 1)[1].split("</span>", 1)[0]
    assert "computed" not in documents


def test_the_totals_still_render(client, monkeypatch):
    """The declaration is additive: the numbers themselves do not move."""
    _totals(monkeypatch)
    normalized = _norm(client.get("/corpus").get_data(as_text=True))
    assert "<b>5</b>" in normalized
    assert "<b>15,200</b>" in normalized
    assert "<b>283,809</b>" in normalized


# --- the GraphQL field ------------------------------------------------------


def test_the_api_exposes_the_same_declaration():
    from doc_ray.entrypoints.api.types import CorpusStats

    stats = CorpusStats.from_row(
        {
            "docs": 5,
            "sents": 15200,
            "lexemes": 30013,
            "occ": 283809,
            "lexemes_computed_at": _ago(minutes=2),
        }
    )
    assert stats.documents == 5
    assert stats.sentences == 15200
    assert stats.lexemes == 30013
    assert stats.occurrences == 283809
    by_figure = {g.figure: g for g in stats.guarantees}
    assert set(by_figure) == {d.figure for d in CORPUS_STAT_GUARANTEES}
    assert by_figure["lexemes"].guarantee == "debounced"
    assert by_figure["lexemes"].staleness_bound_seconds == 600
    assert by_figure["lexemes"].age_seconds == pytest.approx(120, abs=5)
    assert by_figure["documents"].guarantee == "exact"


def test_the_api_marks_a_lagging_scalar_stale():
    from doc_ray.entrypoints.api.types import CorpusStats

    stats = CorpusStats.from_row(
        {
            "docs": 1,
            "sents": 1,
            "lexemes": 1,
            "occ": 1,
            "lexemes_computed_at": _ago(hours=2),
        }
    )
    lexemes = next(g for g in stats.guarantees if g.figure == "lexemes")
    assert lexemes.stale is True


def test_the_graphql_field_is_reachable_from_the_browser():
    """D-034: a capability is shipped when a human can reach it too."""
    import inspect

    from doc_ray.entrypoints.api.query import Query

    assert hasattr(Query, "corpus_stats")
    source = inspect.getsource(webapp.corpus_web)
    assert "corpusStats" in source


# --- the MCP tool -----------------------------------------------------------


def test_the_mcp_surface_registers_the_tool():
    from doc_ray.entrypoints.mcp import corpus

    assert corpus.corpus_stats in corpus.TOOLS


def test_the_mcp_tool_returns_the_totals_with_their_guarantees(monkeypatch):
    from doc_ray.entrypoints.mcp import corpus

    payload = {
        "corpusStats": {
            "documents": 5,
            "sentences": 15200,
            "lexemes": 30013,
            "occurrences": 283809,
            "guarantees": [
                {
                    "figure": "lexemes",
                    "guarantee": "debounced",
                    "stalenessBoundSeconds": 600,
                    "computedAt": _ago().isoformat(),
                    "ageSeconds": 0,
                    "stale": False,
                    "label": "computed 0 seconds ago",
                }
            ],
        }
    }

    async def _gql(query, variables=None):
        assert "corpusStats" in query
        assert "guarantees" in query
        return payload

    monkeypatch.setattr(corpus, "gql", _gql)
    result = asyncio.run(corpus.corpus_stats())
    assert result["documents"] == 5
    assert result["guarantees"][0]["guarantee"] == "debounced"


def test_the_mcp_docstring_tells_an_agent_not_to_trust_a_stale_figure():
    """An agent that cannot see the caveat will quote the number as fact."""
    from doc_ray.entrypoints.mcp import corpus

    doc = corpus.corpus_stats.__doc__ or ""
    assert "stale" in doc.lower()
    assert "guarantee" in doc.lower()


# --- the declaration is single-sourced --------------------------------------


def test_no_surface_invents_its_own_window():
    """Browser, API and MCP all resolve the bound through the domain."""
    assert guarantee_for("lexemes").staleness_bound_seconds == 600
