"""Every figure a read surface prints must declare the guarantee behind it.

The corpus stat band prints four numbers side by side. Three read paths sit
underneath them and they do not offer the same promise: a live count is exact, a
per-document rollup is exact once the job that changed it commits, and the
tenant-wide distinct-lexeme scalar is debounced and may lag. Rendered
identically, the fourth number is a well-formed, plausible, wrong answer that
nothing errors on and no alarm fires for — the only party who can detect it is
the user who just wrote.

This module fixes the declaration in the domain, where it can be asserted
against the database function that actually implements the debounce.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from doc_ray.domain.read_consistency import (
    CORPUS_STAT_GUARANTEES,
    Guarantee,
    ReadPathGuarantee,
    assess,
    guarantee_for,
)

_NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
_MIGRATION = Path(__file__).resolve().parents[3] / "db" / "migrations" / "095_corpus_stats.sql"


# --- the declaration itself -------------------------------------------------


def test_every_figure_on_the_corpus_band_is_declared():
    assert {d.figure for d in CORPUS_STAT_GUARANTEES} == {
        "documents",
        "sentences",
        "lexemes",
        "occurrences",
    }


def test_an_exact_figure_admits_no_staleness():
    documents = guarantee_for("documents")
    assert documents.guarantee is Guarantee.EXACT
    assert documents.staleness_bound_seconds == 0


def test_the_rollup_figures_are_exact_once_their_job_commits():
    for figure in ("sentences", "occurrences"):
        declaration = guarantee_for(figure)
        assert declaration.guarantee is Guarantee.ROLLUP
        assert declaration.staleness_bound_seconds == 0


def test_the_distinct_lexeme_scalar_is_declared_debounced():
    lexemes = guarantee_for("lexemes")
    assert lexemes.guarantee is Guarantee.DEBOUNCED
    assert lexemes.staleness_bound_seconds == 600


def test_the_declared_bound_matches_the_database_debounce():
    """The declaration must track the SQL, not a comment about the SQL.

    ``docray_refresh_stats_for_document`` skips the tenant recompute while the
    last one is younger than ``corpus_max_age``. If that default moves and this
    number does not, the surface would promise a window the database no longer
    honours.
    """
    sql = _MIGRATION.read_text(encoding="utf-8")
    match = re.search(r"corpus_max_age\s+interval\s+DEFAULT\s+interval\s+'(\d+)\s+minutes'", sql)
    assert match is not None, "the debounce default is no longer declared in migration 095"
    assert guarantee_for("lexemes").staleness_bound_seconds == int(match.group(1)) * 60


def test_every_declaration_names_the_relation_it_reads():
    for declaration in CORPUS_STAT_GUARANTEES:
        assert declaration.source, f"{declaration.figure} does not name its read path"
        assert declaration.rationale


def test_an_unknown_figure_is_refused():
    with pytest.raises(KeyError):
        guarantee_for("not-a-figure")


# --- assessing an actual value ----------------------------------------------


def test_an_exact_figure_is_never_stale_and_carries_no_age():
    freshness = assess(guarantee_for("documents"), computed_at=None, now=_NOW)
    assert freshness.age_seconds == 0
    assert freshness.stale is False
    assert freshness.label == "live"


def test_a_debounced_value_inside_its_window_reports_its_age():
    freshness = assess(guarantee_for("lexemes"), computed_at=_NOW - timedelta(minutes=4), now=_NOW)
    assert freshness.age_seconds == 240
    assert freshness.stale is False
    assert "4 minutes ago" in freshness.label


def test_a_debounced_value_past_its_window_is_reported_stale():
    freshness = assess(guarantee_for("lexemes"), computed_at=_NOW - timedelta(minutes=31), now=_NOW)
    assert freshness.stale is True
    assert "31 minutes ago" in freshness.label


def test_the_boundary_of_the_window_is_not_yet_stale():
    freshness = assess(
        guarantee_for("lexemes"), computed_at=_NOW - timedelta(seconds=600), now=_NOW
    )
    assert freshness.stale is False


def test_a_never_computed_debounced_value_is_stale_not_fresh():
    """A rollup that has never run must not read as a current number of zero."""
    freshness = assess(guarantee_for("lexemes"), computed_at=None, now=_NOW)
    assert freshness.stale is True
    assert "never" in freshness.label


def test_a_computed_at_in_the_future_clamps_to_zero_age():
    """Clock skew between the app tier and the database must not print a negative age."""
    freshness = assess(guarantee_for("lexemes"), computed_at=_NOW + timedelta(minutes=5), now=_NOW)
    assert freshness.age_seconds == 0
    assert freshness.stale is False


def test_a_naive_timestamp_is_read_as_utc():
    """psycopg can hand back a naive datetime; it must not raise on subtraction."""
    freshness = assess(
        guarantee_for("lexemes"),
        computed_at=datetime(2026, 7, 28, 11, 55),
        now=_NOW,
    )
    assert freshness.age_seconds == 300


def test_ages_under_a_minute_read_as_seconds():
    freshness = assess(guarantee_for("lexemes"), computed_at=_NOW - timedelta(seconds=12), now=_NOW)
    assert "12 seconds ago" in freshness.label


def test_a_rollup_reports_its_guarantee_rather_than_an_age():
    freshness = assess(guarantee_for("sentences"), computed_at=None, now=_NOW)
    assert freshness.stale is False
    assert "pipeline" in freshness.label


def test_freshness_serializes_for_a_machine_consumer():
    """Agents need the guarantee as data, not as a sentence."""
    payload = assess(
        guarantee_for("lexemes"), computed_at=_NOW - timedelta(minutes=2), now=_NOW
    ).as_dict()
    assert payload["figure"] == "lexemes"
    assert payload["guarantee"] == "debounced"
    assert payload["stalenessBoundSeconds"] == 600
    assert payload["ageSeconds"] == 120
    assert payload["stale"] is False
    assert payload["computedAt"].endswith("+00:00")


def test_a_declaration_is_immutable():
    declaration = guarantee_for("lexemes")
    assert isinstance(declaration, ReadPathGuarantee)
    with pytest.raises((AttributeError, TypeError)):
        declaration.staleness_bound_seconds = 1  # type: ignore[misc]
