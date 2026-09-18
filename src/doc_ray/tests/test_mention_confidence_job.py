"""mention confidence application/job contracts for deterministic mention-confidence scoring."""

from __future__ import annotations

from doc_ray.adapters.mention_confidence_store import MentionFacts
from doc_ray.application import mention_confidence as application
from doc_ray.application.job_handlers import HANDLERS
from doc_ray.domain.jobs import (
    DEFAULT_PIPELINE_CHAIN,
    KIND_ANNOTATE,
    KIND_CANONICALIZE,
    KIND_SCORE_MENTIONS,
    LONG_RUNNING_JOB_KINDS,
    Job,
    JobStatus,
)
from doc_ray.domain.ner_corrections import Mention


def _facts(
    mention_id: str,
    surface: str,
    entity_type: str,
    *,
    type_counts: dict[str, int],
    uses: int,
    resolved: bool,
    crosses: bool = False,
) -> MentionFacts:
    return MentionFacts(
        mention_id=mention_id,
        mention=Mention(0, len(surface), entity_type, surface),
        type_counts=type_counts,
        occurrence_count=uses,
        resolved=resolved,
        crosses_sentence_boundary=crosses,
    )


def test_score_document_materializes_bad_before_good_with_reasons(monkeypatch):
    facts = [
        _facts(
            "bad",
            "Arrakis",
            "LOC",
            type_counts={"LOC": 4, "GPE": 3, "PERSON": 2, "NORP": 1},
            uses=1,
            resolved=False,
        ),
        _facts(
            "good",
            "Duncan Idaho",
            "PERSON",
            type_counts={"PERSON": 200},
            uses=200,
            resolved=True,
        ),
    ]
    written = []
    monkeypatch.setattr(application.store, "load_mention_facts", lambda _conn, _doc: facts)
    monkeypatch.setattr(
        application.store,
        "persist_scores",
        lambda _conn, scores: written.extend(scores) or len(scores),
    )

    result = application.score_document(object(), "doc-a")

    by_id = {row.mention_id: row for row in written}
    assert by_id["bad"].confidence < by_id["good"].confidence
    assert by_id["bad"].reasons
    assert by_id["good"].reasons == ("no heuristic uncertainty signals detected",)
    assert {row.method for row in written} == {"heuristic:v1"}
    assert result == {"document_id": "doc-a", "scored": 2, "method": "heuristic:v1"}


def test_score_document_is_idempotent_for_unchanged_facts(monkeypatch):
    facts = [
        _facts(
            "one",
            "A",
            "PERSON",
            type_counts={"PERSON": 1},
            uses=1,
            resolved=False,
            crosses=True,
        )
    ]
    writes = []
    monkeypatch.setattr(application.store, "load_mention_facts", lambda _conn, _doc: facts)
    monkeypatch.setattr(
        application.store,
        "persist_scores",
        lambda _conn, scores: writes.append(tuple(scores)) or len(scores),
    )
    application.score_document(object(), "doc-a")
    application.score_document(object(), "doc-a")
    assert writes[0] == writes[1]


def test_score_job_reports_progress_and_returns_json_summary(monkeypatch):
    progress = []
    monkeypatch.setattr(application, "report_progress", lambda *args: progress.append(args))
    monkeypatch.setattr(
        application,
        "score_document",
        lambda _conn, document_id: {
            "document_id": document_id,
            "scored": 3,
            "method": "heuristic:v1",
        },
    )
    job = Job("job-a", KIND_SCORE_MENTIONS, JobStatus.RUNNING, document_id="doc-a")
    result = application.handle_score_mentions(object(), None, job)
    assert result == {"document_id": "doc-a", "scored": 3, "method": "heuristic:v1"}
    assert progress == [(0, 1, "Scoring entity mentions"), (1, 1, "Entity mentions scored")]


def test_score_job_requires_a_document():
    job = Job("job-a", KIND_SCORE_MENTIONS, JobStatus.RUNNING)
    try:
        application.handle_score_mentions(object(), None, job)
    except RuntimeError as error:
        assert str(error) == "score_mentions requires a document_id"
    else:
        raise AssertionError("missing document_id was accepted")


def test_score_job_runs_after_every_mention_minting_step_and_stays_on_fast_lane():
    """Scoring must follow annotation AND canonicalization, not just annotation.

    This test previously pinned score_mentions to ``annotate + 1``. That position
    is what made 131,172 mentions unscorable: canonicalization's registry scan
    mints a ``term``-layer entity_mention per matched form, and at position 2 the
    scorer ran before those rows existed, so their confidence stayed NULL for good
    (100% of that layer, across every document). The invariant
    is "after everything that creates mentions", not "immediately after annotate".
    """
    scoring_index = DEFAULT_PIPELINE_CHAIN.index(KIND_SCORE_MENTIONS)
    for minting_step in (KIND_ANNOTATE, KIND_CANONICALIZE):
        assert DEFAULT_PIPELINE_CHAIN.index(minting_step) < scoring_index
    assert HANDLERS[KIND_SCORE_MENTIONS] is application.handle_score_mentions
    # Still a fast-lane job: it is deterministic arithmetic over existing facts,
    # so it must not be routed to the GPU pool.
    assert KIND_SCORE_MENTIONS not in LONG_RUNNING_JOB_KINDS
