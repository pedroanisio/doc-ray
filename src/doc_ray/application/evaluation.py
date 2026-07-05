"""Evaluation runs and deterministic corpus-drift metrics."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.types.json import Json

from doc_ray.domain.ulid import new_ulid_str


@dataclass(frozen=True)
class EvaluationRunResult:
    """Identifiers created for a persisted evaluation run."""

    snapshot_id: str
    run_id: str


def _scalar(conn: psycopg.Connection[Any], sql: str, params: tuple[Any, ...] = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    assert row is not None
    return int(next(iter(row.values())))


def _schema_version(conn: psycopg.Connection[Any]) -> str:
    row = conn.execute("SELECT docray_schema_version() AS version").fetchone()
    assert row is not None
    return str(row["version"])


def _json_count_map(
    conn: psycopg.Connection[Any], sql: str, params: tuple[Any, ...] = ()
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in conn.execute(sql, params):
        counts[str(row["key"])] += 1
    return dict(sorted(counts.items()))


def _scope_predicate(alias: str, document_ids: list[str] | None) -> tuple[str, tuple[Any, ...]]:
    if document_ids is None:
        return "", ()
    return f" WHERE {alias}.document_id = ANY(%s)", (document_ids,)


def collect_snapshot(
    conn: psycopg.Connection[Any],
    *,
    source: str,
    document_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Collect deterministic corpus facts for evaluation and drift comparison."""
    doc_where, doc_params = (
        (" WHERE id = ANY(%s)", (document_ids,)) if document_ids is not None else ("", ())
    )
    sent_where, sent_params = _scope_predicate("sentences", document_ids)
    ann_where, ann_params = _scope_predicate("annotations", document_ids)
    lexeme_where, lexeme_params = _scope_predicate("lexeme_occurrence_detail", document_ids)
    lexeme_distribution = _json_count_map(
        conn,
        f"""
        SELECT lemma AS key
          FROM lexeme_occurrence_detail
          {lexeme_where}
        """,
        lexeme_params,
    )
    entity_where = ""
    entity_params: tuple[Any, ...] = ()
    if document_ids is not None:
        entity_where = " WHERE a.document_id = ANY(%s)"
        entity_params = (document_ids,)
    entity_distribution = _json_count_map(
        conn,
        f"""
        SELECT entity_type AS key
          FROM entity_mention em
          JOIN annotations a ON a.id = em.annotation_id
          {entity_where}
        """,
        entity_params,
    )
    tool_versions = {
        str(row["tool"]): str(row["tool_version"] or "unknown")
        for row in conn.execute(
            f"""
            SELECT tool, max(tool_version) AS tool_version
              FROM annotations
              {ann_where}
             GROUP BY tool
             ORDER BY tool
            """,
            ann_params,
        ).fetchall()
    }
    review_join = ""
    review_where = ""
    review_params: tuple[Any, ...] = ()
    if document_ids is not None:
        review_join = (
            " JOIN entity_mention em ON em.id = resolution_review.mention_id"
            " JOIN annotations a ON a.id = em.annotation_id"
        )
        review_where = " WHERE a.document_id = ANY(%s)"
        review_params = (document_ids,)
    review_counts = {
        str(row["status"]): int(row["count"])
        for row in conn.execute(
            f"""
            SELECT status, count(*) AS count
              FROM resolution_review
              {review_join}
              {review_where}
             GROUP BY status
            """,
            review_params,
        ).fetchall()
    }
    token_sql = (
        "SELECT count(*) FROM tokens JOIN annotations ON annotations.id = tokens.annotation_id"
    )
    lex_occ_sql = (
        "SELECT count(*) FROM lexeme_occurrences "
        "JOIN annotations ON annotations.id = lexeme_occurrences.annotation_id"
    )
    entity_sql = (
        "SELECT count(*) FROM entity_mention "
        "JOIN annotations ON annotations.id = entity_mention.annotation_id"
    )
    link_sql = (
        "SELECT count(*) FROM entity_link "
        "JOIN entity_mention ON entity_mention.id = entity_link.mention_id "
        "JOIN annotations ON annotations.id = entity_mention.annotation_id"
    )
    return {
        "id": new_ulid_str(),
        "source": source,
        "schema_version": _schema_version(conn),
        "document_count": _scalar(conn, f"SELECT count(*) FROM documents{doc_where}", doc_params),
        "sentence_count": _scalar(conn, f"SELECT count(*) FROM sentences{sent_where}", sent_params),
        "token_count": _scalar(conn, f"{token_sql}{ann_where}", ann_params),
        "lexeme_count": len(lexeme_distribution),
        "lexeme_occurrence_count": _scalar(conn, f"{lex_occ_sql}{ann_where}", ann_params),
        "entity_mention_count": _scalar(conn, f"{entity_sql}{ann_where}", ann_params),
        "entity_link_count": _scalar(conn, f"{link_sql}{ann_where}", ann_params),
        "review_pending_count": review_counts.get("pending", 0),
        "review_approved_count": review_counts.get("approved", 0),
        "review_rejected_count": review_counts.get("rejected", 0),
        "tool_versions": tool_versions,
        "lexeme_distribution": lexeme_distribution,
        "entity_distribution": entity_distribution,
        "fact_refs": {
            "lexeme_distribution": "lexeme_occurrence_detail",
            "entity_distribution": "entity_mention",
            "tool_versions": "annotations",
            "review_counts": "resolution_review",
        },
    }


def _distribution_metrics(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    snapshot_id = str(snapshot["id"])
    for subject_id, count in sorted(snapshot["lexeme_distribution"].items()):
        metrics.append(
            {
                "name": "lexeme_frequency",
                "subject": "lexeme",
                "subject_id": subject_id,
                "value_numeric": count,
                "unit": "count",
                "fact_refs": {
                    "snapshot_id": snapshot_id,
                    "source": "lexeme_occurrence_detail",
                },
            }
        )
    for subject_id, count in sorted(snapshot["entity_distribution"].items()):
        metrics.append(
            {
                "name": "entity_frequency",
                "subject": "entity_type",
                "subject_id": subject_id,
                "value_numeric": count,
                "unit": "count",
                "fact_refs": {"snapshot_id": snapshot_id, "source": "entity_mention"},
            }
        )
    return metrics


def compare_snapshots(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compare two corpus snapshots with stable lexical/entity delta metrics."""
    metrics: list[dict[str, Any]] = []
    refs = {
        "baseline_snapshot_id": str(baseline["id"]),
        "candidate_snapshot_id": str(candidate["id"]),
    }
    for subject_id in sorted(
        set(baseline["entity_distribution"]) | set(candidate["entity_distribution"])
    ):
        delta = candidate["entity_distribution"].get(subject_id, 0) - baseline[
            "entity_distribution"
        ].get(subject_id, 0)
        if delta:
            metrics.append(
                {
                    "name": "entity_frequency_delta",
                    "subject": "entity_type",
                    "subject_id": subject_id,
                    "value_numeric": delta,
                    "unit": "count",
                    "fact_refs": refs,
                }
            )
    for subject_id in sorted(
        set(baseline["lexeme_distribution"]) | set(candidate["lexeme_distribution"])
    ):
        delta = candidate["lexeme_distribution"].get(subject_id, 0) - baseline[
            "lexeme_distribution"
        ].get(subject_id, 0)
        if delta:
            metrics.append(
                {
                    "name": "lexeme_frequency_delta",
                    "subject": "lexeme",
                    "subject_id": subject_id,
                    "value_numeric": delta,
                    "unit": "count",
                    "fact_refs": refs,
                }
            )
    return metrics


def _insert_metrics(
    conn: psycopg.Connection[Any],
    *,
    run_id: str,
    metrics: list[dict[str, Any]],
) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO evaluation_metric
                (run_id, name, subject, subject_id, value_numeric, value_json, unit, fact_refs)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    run_id,
                    metric["name"],
                    metric["subject"],
                    metric["subject_id"],
                    metric.get("value_numeric"),
                    Json(metric.get("value_json", {})),
                    metric.get("unit", "count"),
                    Json(metric.get("fact_refs", {})),
                )
                for metric in metrics
            ],
        )


def create_evaluation_run(
    conn: psycopg.Connection[Any],
    *,
    source: str,
    evaluator: str,
    evaluator_version: str,
    note: str | None = None,
    document_ids: list[str] | None = None,
) -> EvaluationRunResult:
    """Persist one corpus snapshot, one evaluation run, and its base metrics."""
    snapshot = collect_snapshot(conn, source=source, document_ids=document_ids)
    run_id = new_ulid_str()
    with conn.transaction():
        conn.execute(
            """
            INSERT INTO corpus_snapshot
                (id, source, schema_version, document_count, sentence_count, token_count,
                 lexeme_count, lexeme_occurrence_count, entity_mention_count,
                 entity_link_count, review_pending_count, review_approved_count,
                 review_rejected_count, tool_versions, lexeme_distribution,
                 entity_distribution, fact_refs)
            VALUES
                (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                snapshot["id"],
                snapshot["source"],
                snapshot["schema_version"],
                snapshot["document_count"],
                snapshot["sentence_count"],
                snapshot["token_count"],
                snapshot["lexeme_count"],
                snapshot["lexeme_occurrence_count"],
                snapshot["entity_mention_count"],
                snapshot["entity_link_count"],
                snapshot["review_pending_count"],
                snapshot["review_approved_count"],
                snapshot["review_rejected_count"],
                Json(snapshot["tool_versions"]),
                Json(snapshot["lexeme_distribution"]),
                Json(snapshot["entity_distribution"]),
                Json(snapshot["fact_refs"]),
            ),
        )
        conn.execute(
            """
            INSERT INTO evaluation_run
                (id, snapshot_id, evaluator, evaluator_version, schema_version, note)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                run_id,
                snapshot["id"],
                evaluator,
                evaluator_version,
                snapshot["schema_version"],
                note,
            ),
        )
        _insert_metrics(conn, run_id=run_id, metrics=_distribution_metrics(snapshot))
    return EvaluationRunResult(snapshot_id=str(snapshot["id"]), run_id=run_id)
