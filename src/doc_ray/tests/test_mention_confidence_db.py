"""Schema and persistence contracts for Block G mention-confidence materialization."""

from __future__ import annotations

from pathlib import Path

import pytest

import doc_ray
from doc_ray.adapters.mention_confidence_store import (
    StoredScore,
    load_mention_facts,
    persist_scores,
)

_ROOT = Path(__file__).resolve().parents[3]


def test_mention_confidence_migration_is_nullable_constrained_and_rank_indexed():
    sql = (_ROOT / "db/migrations/138_mention_confidence.sql").read_text(encoding="utf-8")
    normalized = " ".join(sql.lower().split())
    assert "add column if not exists confidence real" in normalized
    assert "add column if not exists confidence_method text" in normalized
    assert "add column if not exists confidence_reasons text[]" in normalized
    assert "confidence >= 0" in normalized and "confidence <= 1" in normalized
    assert "(annotation_id, confidence, id)" in normalized
    assert "triage rank, not a calibrated probability" in normalized


def test_mention_confidence_schema_change_records_its_release_without_freezing_head():
    marker = _ROOT / "db/migrations/139_schema_version_1_50_0.sql"
    assert marker.is_file()
    assert "'1.50.0'" in marker.read_text(encoding="utf-8")
    assert tuple(map(int, doc_ray.__version__.split("."))) >= (1, 50, 0)


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Connection:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), params))
        rows = self.responses.pop(0) if self.responses else []
        return _Cursor(rows)


def test_load_facts_normalizes_surface_variants_and_reuses_tenant_rollup():
    conn = _Connection(
        [
            [
                {
                    "mention_id": "01MENTION",
                    "char_start": 0,
                    "char_end": 7,
                    "entity_type": "LOC",
                    "surface": "Arrákis",
                    "tenant_id": "tenant-a",
                    "crosses_sentence_boundary": False,
                }
            ],
            [
                {"entity_text": "ARRAKIS", "entity_type": "LOC", "uses": 6},
                {"entity_text": "Arrakis", "entity_type": "GPE", "uses": 4},
                {"entity_text": "Other", "entity_type": "PERSON", "uses": 99},
            ],
            [{"form_norm": "arrakis"}],
        ]
    )
    (facts,) = load_mention_facts(conn, "doc-a")
    assert facts.mention_id == "01MENTION"
    assert facts.mention.surface == "Arrákis"
    assert facts.type_counts == {"LOC": 6, "GPE": 4}
    assert facts.occurrence_count == 10
    assert facts.resolved is True
    assert facts.crosses_sentence_boundary is False
    assert conn.calls[1][1] == ("tenant-a",)
    assert conn.calls[2][1] == (["arrakis"],)


def test_load_facts_marks_unresolved_and_cross_sentence_mentions():
    conn = _Connection(
        [
            [
                {
                    "mention_id": "01MENTION",
                    "char_start": 4,
                    "char_end": 12,
                    "entity_type": "PERSON",
                    "surface": "Duncan 2",
                    "tenant_id": "tenant-a",
                    "crosses_sentence_boundary": True,
                }
            ],
            [{"entity_text": "Duncan 2", "entity_type": "PERSON", "uses": 1}],
            [],
        ]
    )
    (facts,) = load_mention_facts(conn, "doc-a")
    assert facts.resolved is False
    assert facts.crosses_sentence_boundary is True


def test_load_facts_with_no_mentions_avoids_tenant_wide_queries():
    conn = _Connection([[]])
    assert load_mention_facts(conn, "empty-doc") == []
    assert len(conn.calls) == 1


def test_persist_scores_is_repeatable_and_writes_explanations_with_the_rank():
    conn = _Connection()
    rows = [StoredScore("01MENTION", 0.42, "heuristic:v1", ("type disagrees", "rare"))]
    assert persist_scores(conn, rows) == 1
    assert persist_scores(conn, rows) == 1
    assert conn.calls[0] == conn.calls[1]
    sql, params = conn.calls[0]
    assert "UPDATE entity_mention" in sql
    assert "confidence_reasons = %s" in sql
    assert params == (0.42, "heuristic:v1", ["type disagrees", "rare"], "01MENTION")


@pytest.mark.parametrize(
    "row",
    [
        StoredScore("01MENTION", 0.0, "heuristic:v1", ("uncertain",)),
        StoredScore("01MENTION", 1.0, "heuristic:v1", ("clean",)),
    ],
)
def test_stored_score_accepts_boundary_values(row):
    assert row.confidence in {0.0, 1.0}


@pytest.mark.parametrize(
    ("confidence", "method", "reasons", "message"),
    [
        (-0.01, "heuristic:v1", ("bad",), "confidence"),
        (1.01, "heuristic:v1", ("bad",), "confidence"),
        (0.5, "", ("bad",), "method"),
        (0.5, "heuristic:v1", (), "reason"),
    ],
)
def test_stored_score_rejects_malformed_materialization(confidence, method, reasons, message):
    with pytest.raises(ValueError, match=message):
        StoredScore("01MENTION", confidence, method, reasons)


def test_stored_score_requires_a_mention_identity():
    with pytest.raises(ValueError, match="mention_id"):
        StoredScore("", 0.5, "heuristic:v1", ("uncertain",))
