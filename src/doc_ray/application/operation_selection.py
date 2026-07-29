"""One definition of "which documents does this operation still owe work for".

WHY THIS MODULE EXISTS. The same predicate has to answer two different questions:
the console's *status* ("269 documents pending") and the runner's *selection*
("enqueue exactly those documents"). If those drift apart the console lies — it
reports a backlog that the run does not clear, or reports zero while work
remains. Both therefore read from the table below, and nothing else may define
eligibility.

The SQL is intentionally the same predicate the operator CLIs have always used,
so this is a consolidation rather than a behaviour change: an operation enqueued
from the browser selects precisely what ``scripts/enqueue_visual_backfill.py``
would have selected from a shell.

D-024 NOTE. ``pending_counts`` aggregates over ``document_image``/``sentences``,
which is a live count over fact tables. It is admin-only and off every user read
path — it exists so an operator can size a sweep before firing it — and it is
never joined into a corpus read. That is the D-024 boundary, not a violation of
it: the rule bars aggregates on read paths, and this is an operations surface.
"""

from __future__ import annotations

from typing import Any

from doc_ray.adapters.text_encoder_sentence_transformers import DEFAULT_MODEL_TAG
from doc_ray.domain.operations import get_operation

#: Per-operation eligibility. ``base`` is the always-true half (what the operation
#: can act on at all); ``missing`` is the anti-join that makes ``only_missing``
#: re-runnable after a completed sweep.
_BASE: dict[str, str] = {
    "visual_extract": (
        "d.status = 'extracted' AND d.doc_type = 'pdf' AND d.source_uri IS NOT NULL"
    ),
    "visual_embed": (
        "d.status = 'extracted' "
        "AND EXISTS (SELECT 1 FROM document_image di WHERE di.document_id = d.id)"
    ),
    "visual_caption": (
        "d.status = 'extracted' "
        "AND EXISTS (SELECT 1 FROM document_image di WHERE di.document_id = d.id)"
    ),
    "semantic_embed": "EXISTS (SELECT 1 FROM sentences s WHERE s.document_id = d.id)",
    "reextract": ("d.status = 'extracted' AND d.doc_type = 'pdf' AND d.source_uri IS NOT NULL"),
    # A repair, so the eligible set is the damaged one: ingest kept the bytes for
    # exactly this reason, and a failed document with no blob is unrecoverable.
    "retry_extract": "d.status = 'failed' AND d.source_uri IS NOT NULL",
}

_MISSING: dict[str, str] = {
    "visual_extract": ("NOT EXISTS (SELECT 1 FROM document_image di WHERE di.document_id = d.id)"),
    "visual_embed": ("NOT EXISTS (SELECT 1 FROM image_embedding ie WHERE ie.document_id = d.id)"),
    "visual_caption": ("NOT EXISTS (SELECT 1 FROM visual_caption vc WHERE vc.document_id = d.id)"),
    "semantic_embed": (
        "NOT EXISTS ("
        " SELECT 1 FROM embedding e JOIN sentences s2 ON s2.span_id = e.span_id"
        " WHERE s2.document_id = d.id AND e.model = %(model)s)"
    ),
    "reextract": (
        "NOT EXISTS ("
        " SELECT 1 FROM spans sp WHERE sp.document_id = d.id AND sp.page_number IS NOT NULL)"
    ),
    # ``status='failed'`` already means "owes text"; a successful repair promotes
    # the document out of the base set, so there is nothing further to subtract.
    "retry_extract": "true",
}

#: The unit an operator actually cares about when sizing a sweep. Images for the
#: visual tiers, sentences for the semantic one; None where the document count is
#: the whole story.
_UNIT: dict[str, tuple[str, str] | None] = {
    "visual_extract": None,
    "visual_embed": (
        "images",
        "SELECT count(*) FROM document_image di WHERE di.document_id = d.id",
    ),
    "visual_caption": (
        "images",
        "SELECT count(*) FROM document_image di WHERE di.document_id = d.id",
    ),
    "semantic_embed": ("sentences", "SELECT count(*) FROM sentences s WHERE s.document_id = d.id"),
    "reextract": None,
    "retry_extract": None,
}


def eligibility_where(key: str, *, only_missing: bool) -> str:
    """The single eligibility predicate both the runner and the status view use.

    Exposed by name rather than inlined so the equivalence is assertable: a test
    can check that both queries embed *this* string, instead of reconstructing it
    by splitting SQL and accidentally matching a nested subquery's WHERE.
    """
    get_operation(key)  # reject unknown keys before building SQL
    where = _BASE[key]
    if only_missing:
        where = f"{where} AND {_MISSING[key]}"
    return where


def selection_sql(key: str, *, only_missing: bool) -> tuple[str, dict[str, Any]]:
    """Return ``(sql, params)`` selecting eligible document ids in enqueue order.

    Ordering is ``created_at, id`` for every operation — the order the CLIs have
    always used — so a partially drained sweep resumes predictably rather than
    re-shuffling on each run.
    """
    where = eligibility_where(key, only_missing=only_missing)
    sql = f"SELECT d.id FROM documents d WHERE {where} ORDER BY d.created_at, d.id"
    return sql, _params_for(key)


def count_sql(key: str, *, only_missing: bool) -> tuple[str, dict[str, Any]]:
    """Return ``(sql, params)`` counting pending documents and their pending units."""
    where = eligibility_where(key, only_missing=only_missing)
    unit = _UNIT[key]
    unit_expr = f"coalesce(sum(({unit[1]})), 0)" if unit else "0"
    sql = (
        f"SELECT count(*) AS documents, {unit_expr} AS units "  # noqa: S608 — fixed fragments
        f"FROM documents d WHERE {where}"
    )
    return sql, _params_for(key)


def unit_label(key: str) -> str | None:
    """Name of the pending unit ('images', 'sentences'), or None if documents suffice."""
    unit = _UNIT[key]
    return unit[0] if unit else None


def _params_for(key: str) -> dict[str, Any]:
    # Only the semantic selection is model-scoped; passing an unused key would
    # make psycopg raise on the operations that take no parameters.
    return {"model": DEFAULT_MODEL_TAG} if key == "semantic_embed" else {}
