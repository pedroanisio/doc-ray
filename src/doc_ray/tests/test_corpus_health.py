"""Tests for the corpus health report (unit: pure issue rules; integration: live DB).

The issue-derivation rules are pure functions over a snapshot dict, so they run
without a database. ``collect``/CLI tests use the ``pg`` fixture and skip
cleanly when Postgres is unreachable.
"""

from __future__ import annotations

import json

from doc_ray.application import corpus_health as ch

# --- snapshot builders -------------------------------------------------------


def _snapshot(**over):
    snap = {
        "totals": {
            "documents": 1,
            "sentences": 10,
            "tokens": 100,
            "entity_mentions": 5,
            "entity_links": 1,
            "canonical_entries": 3,
            "lexemes": 50,
            "embeddings": 2,
        },
        "documents": [
            {
                "id": "d1",
                "title": "healthy doc",
                "language": "en",
                "status": "extracted",
                "sentences": 10,
                "mentions": 5,
                "links": 1,
            }
        ],
        "jobs": [{"kind": "ingest", "status": "succeeded", "count": 1}],
        "running_jobs": [],
    }
    snap.update(over)
    return snap


def _codes(issues):
    return [i["code"] for i in issues]


# --- unit: issue rules --------------------------------------------------------


def test_healthy_snapshot_has_no_issues():
    assert ch.find_issues(_snapshot()) == []


def test_null_language_flags_fts_degraded():
    snap = _snapshot(
        documents=[
            {
                "id": "d1",
                "title": "t",
                "language": None,
                "status": "extracted",
                "sentences": 4,
                "mentions": 2,
                "links": 0,
            }
        ]
    )
    issues = ch.find_issues(snap)
    assert "fts-degraded" in _codes(issues)
    hit = next(i for i in issues if i["code"] == "fts-degraded")
    assert hit["severity"] == "warning"
    assert hit["document_id"] == "d1"


def test_extracted_doc_without_sentences_is_an_error():
    snap = _snapshot(
        documents=[
            {
                "id": "d1",
                "title": "t",
                "language": "en",
                "status": "extracted",
                "sentences": 0,
                "mentions": 0,
                "links": 0,
            }
        ]
    )
    issues = ch.find_issues(snap)
    assert "document-empty" in _codes(issues)
    assert next(i for i in issues if i["code"] == "document-empty")["severity"] == "error"
    # An empty doc must not also be reported as unannotated/degraded noise.
    assert "document-unannotated" not in _codes(issues)
    assert "fts-degraded" not in _codes(issues)


def test_doc_with_sentences_but_no_mentions_flags_unannotated():
    snap = _snapshot(
        documents=[
            {
                "id": "d1",
                "title": "t",
                "language": "en",
                "status": "extracted",
                "sentences": 8,
                "mentions": 0,
                "links": 0,
            }
        ]
    )
    assert "document-unannotated" in _codes(ch.find_issues(snap))


def test_failed_jobs_are_errors():
    snap = _snapshot(jobs=[{"kind": "ingest", "status": "failed", "count": 2}])
    issues = ch.find_issues(snap)
    hit = next(i for i in issues if i["code"] == "job-failed")
    assert hit["severity"] == "error"
    assert "2" in hit["detail"] and "ingest" in hit["detail"]


def test_stalled_running_job_flags_warning():
    snap = _snapshot(
        running_jobs=[
            {
                "id": "j1",
                "kind": "annotate",
                "locked_by": "w1",
                "heartbeat_age_s": ch.STALE_HEARTBEAT_SECONDS + 1,
            }
        ]
    )
    assert "job-stalled" in _codes(ch.find_issues(snap))


def test_fresh_running_job_is_not_stalled():
    snap = _snapshot(
        running_jobs=[{"id": "j1", "kind": "annotate", "locked_by": "w1", "heartbeat_age_s": 5}]
    )
    assert "job-stalled" not in _codes(ch.find_issues(snap))


def test_missing_embeddings_is_info_only():
    snap = _snapshot(totals={**_snapshot()["totals"], "embeddings": 0})
    issues = ch.find_issues(snap)
    hit = next(i for i in issues if i["code"] == "embeddings-missing")
    assert hit["severity"] == "info"


def test_exit_code_ignores_info_but_not_warnings():
    assert ch.exit_code([]) == 0
    assert ch.exit_code([{"code": "x", "severity": "info", "detail": ""}]) == 0
    assert ch.exit_code([{"code": "x", "severity": "warning", "detail": ""}]) == 2
    assert ch.exit_code([{"code": "x", "severity": "error", "detail": ""}]) == 2


# --- unit: rendering ----------------------------------------------------------


def test_render_text_shows_totals_and_issues():
    snap = _snapshot(
        documents=[
            {
                "id": "d1",
                "title": "broken",
                "language": None,
                "status": "extracted",
                "sentences": 4,
                "mentions": 0,
                "links": 0,
            }
        ]
    )
    out = ch.render_text(snap, ch.find_issues(snap))
    assert "documents" in out and "1" in out
    assert "fts-degraded" in out
    assert "broken" in out


def test_render_text_healthy_says_so():
    out = ch.render_text(_snapshot(), [])
    assert "no issues" in out.lower()


# --- integration: live DB -----------------------------------------------------


def test_collect_and_issues_against_live_db(pg):
    import _itdb

    conn, created = pg
    doc_id = _itdb.make_doc(conn, "Elena founded a startup in Berlin. It grew fast.")
    created.append(doc_id)

    snap = ch.collect(conn)
    assert snap["totals"]["documents"] >= 1
    mine = [d for d in snap["documents"] if str(d["id"]) == str(doc_id)]
    assert mine and mine[0]["sentences"] == 2

    # make_doc stores no language and no annotations: both rules must fire.
    issues = ch.find_issues(snap)
    ids = [(i["code"], str(i.get("document_id"))) for i in issues]
    assert ("fts-degraded", str(doc_id)) in ids
    assert ("document-unannotated", str(doc_id)) in ids


def test_cli_json_output(pg, monkeypatch, capsys):
    import _itdb

    monkeypatch.setenv("DATABASE_URL", _itdb.DB_URL)
    rc = ch.main(["--json"])
    assert rc in (0, 2)
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"totals", "documents", "jobs", "running_jobs", "issues"}


# --- pipeline-stalled --------------------------------------------------------
#
# The 2026-07-30 incident: 55 documents whose `annotate` job spent all three
# attempts against a saturated GPU. Because `annotate` heads the pipeline chain,
# nothing downstream was ever enqueued, and nothing re-enqueues a failed job.
# Health reported them only as "annotation never ran", which reads like a stage
# still waiting its turn rather than one that has permanently stopped.


def test_exhausted_stage_is_reported_as_stalled_not_merely_unannotated():
    snap = _snapshot(
        documents=[
            {
                "id": "d1",
                "title": "stranded doc",
                "language": "en",
                "status": "extracted",
                "sentences": 120,
                "mentions": 0,
                "links": 0,
                "stalled_kinds": ["annotate"],
            }
        ]
    )
    issues = ch.find_issues(snap)
    codes = {i["code"] for i in issues}
    assert "pipeline-stalled" in codes, codes
    stalled = next(i for i in issues if i["code"] == "pipeline-stalled")
    assert stalled["severity"] == "error"
    assert "annotate" in stalled["detail"]
    assert stalled["document_id"] == "d1"


def test_a_stalled_document_fails_the_exit_code():
    """An operator running this in a check must be told, not merely warned."""
    snap = _snapshot(
        documents=[
            {
                "id": "d1",
                "title": "stranded doc",
                "language": "en",
                "status": "extracted",
                "sentences": 120,
                "mentions": 0,
                "links": 0,
                "stalled_kinds": ["annotate", "semantic_embed"],
            }
        ]
    )
    assert ch.exit_code(ch.find_issues(snap)) == 2


def test_absent_or_empty_stalled_kinds_changes_nothing():
    """A retried-and-succeeded stage must not linger as an issue."""
    for value in (None, []):
        snap = _snapshot(
            documents=[
                {
                    "id": "d1",
                    "title": "healthy doc",
                    "language": "en",
                    "status": "extracted",
                    "sentences": 10,
                    "mentions": 5,
                    "links": 1,
                    "stalled_kinds": value,
                }
            ]
        )
        assert not [i for i in ch.find_issues(snap) if i["code"] == "pipeline-stalled"]
