"""Evaluation and drift artifact tests."""

from __future__ import annotations

import _itdb

from doc_ray.adapters import persistence as db
from doc_ray.application import evaluation as ev
from doc_ray.domain.ulid import new_ulid_str


def _cols(conn, table: str) -> set[str]:
    return {
        r["column_name"]
        for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name=%s", (table,)
        ).fetchall()
    }


def test_evaluation_tables_exist(pg):
    conn, _created = pg
    assert {
        "id",
        "schema_version",
        "document_count",
        "lexeme_distribution",
        "entity_distribution",
        "tool_versions",
    } <= _cols(conn, "corpus_snapshot")
    assert {"id", "snapshot_id", "evaluator", "evaluator_version"} <= _cols(conn, "evaluation_run")
    assert {"run_id", "name", "subject", "subject_id", "fact_refs"} <= _cols(
        conn, "evaluation_metric"
    )
    assert {"id", "document_id", "span_id", "label", "expected"} <= _cols(conn, "gold_sample")


def test_snapshot_run_records_distributions_and_fact_citations(pg):
    conn, created = pg
    doc_id = _itdb.make_doc(conn, "Alpha beta beta met Ada.")
    created.append(doc_id)
    annotation_id = _itdb.make_annotation(conn, doc_id, "lexeme", "pytest/eval")
    tokens = _itdb.make_tokens(conn, doc_id, annotation_id, "Alpha beta beta met Ada.")
    cache: dict[str, str] = {}
    lexeme_uids = {
        "alpha": db.get_or_create_lexeme(conn, cache, "alpha", "NOUN", "en")[0],
        "beta": db.get_or_create_lexeme(conn, cache, "beta", "NOUN", "en")[0],
        "met": db.get_or_create_lexeme(conn, cache, "meet", "VERB", "en")[0],
        "ada": db.get_or_create_lexeme(conn, cache, "ada", "PROPN", "en")[0],
    }
    for token in tokens:
        lemma = token["surface"].rstrip(".").casefold()
        uid = lexeme_uids[lemma]
        conn.execute(
            "UPDATE tokens SET lexeme_uid=%s WHERE id=%s",
            (uid, token["id"]),
        )
        conn.execute(
            "INSERT INTO lexeme_occurrences (annotation_id, span_id, lexeme_uid) "
            "VALUES (%s, %s, %s)",
            (annotation_id, token["span_id"], uid),
        )
    mention_id = new_ulid_str()
    conn.execute(
        "INSERT INTO entity_mention (id, annotation_id, span_id, entity_type, surface) "
        "VALUES (%s, %s, %s, 'PER', %s)",
        (mention_id, annotation_id, tokens[-1]["span_id"], tokens[-1]["surface"]),
    )

    result = ev.create_evaluation_run(
        conn,
        source="fixture",
        evaluator="pytest",
        evaluator_version="1.0",
        note="drift fixture",
        document_ids=[doc_id],
    )

    snapshot = conn.execute(
        "SELECT * FROM corpus_snapshot WHERE id = %s", (result.snapshot_id,)
    ).fetchone()
    assert snapshot["document_count"] == 1
    assert snapshot["lexeme_distribution"]["beta"] == 2
    assert snapshot["entity_distribution"]["PER"] == 1
    assert snapshot["tool_versions"]["pytest/eval"] == "unknown"
    metric = conn.execute(
        "SELECT * FROM evaluation_metric WHERE run_id = %s AND name = %s AND subject_id = %s",
        (result.run_id, "lexeme_frequency", "beta"),
    ).fetchone()
    assert metric["value_numeric"] == 2
    assert metric["fact_refs"]["snapshot_id"] == result.snapshot_id
    assert metric["fact_refs"]["source"] == "lexeme_occurrence_detail"


def test_compare_snapshots_reports_deterministic_lexical_and_entity_drift():
    baseline = {
        "id": "snap-a",
        "lexeme_distribution": {"alpha": 2, "beta": 1},
        "entity_distribution": {"PER": 1},
    }
    candidate = {
        "id": "snap-b",
        "lexeme_distribution": {"alpha": 1, "gamma": 3},
        "entity_distribution": {"ORG": 2, "PER": 1},
    }

    metrics = ev.compare_snapshots(baseline, candidate)

    keys = [(m["name"], m["subject"], m["subject_id"]) for m in metrics]
    assert keys == [
        ("entity_frequency_delta", "entity_type", "ORG"),
        ("lexeme_frequency_delta", "lexeme", "alpha"),
        ("lexeme_frequency_delta", "lexeme", "beta"),
        ("lexeme_frequency_delta", "lexeme", "gamma"),
    ]
    gamma = metrics[-1]
    assert gamma["value_numeric"] == 3
    assert gamma["fact_refs"] == {
        "baseline_snapshot_id": "snap-a",
        "candidate_snapshot_id": "snap-b",
    }
