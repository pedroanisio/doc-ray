"""GraphQL pipeline surface: jobs query + annotate/canonicalize mutations (skip-clean).

The webapp's upload flow contract: after ingestDocument, a client renders
pipeline progress from ``jobs(documentId:)`` and can trigger the individual
steps explicitly. All writes enqueue worker jobs — nothing runs in-request.
"""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

from doc_ray.adapters import queue_postgres as queue


def _connect_or_skip():
    import _itdb

    try:
        return _itdb.connect()
    except Exception as exc:  # pragma: no cover - env dependent
        pytest.skip(f"DB unreachable: {exc}")


@pytest.fixture
def api_env():
    import _itdb

    conn = _connect_or_skip()
    doc_id = _itdb.make_doc(conn, "Pipeline zqorvithex test. Second sentence.")
    principal = conn.execute(
        "SELECT id FROM principals WHERE display_name = 'bootstrap-admin'"
    ).fetchone()
    if principal is None:
        pytest.skip("RBAC seed principal absent")
    try:
        yield conn, doc_id, str(principal["id"])
    finally:
        conn.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
        conn.close()


def _client(principal_id: str):
    import _itdb
    from fastapi.testclient import TestClient

    from doc_ray.config import Config
    from doc_ray.entrypoints.api.app import create_app

    cfg = replace(
        Config.from_env(),
        database_url=_itdb.DB_URL,
        api_tokens={"test-token": (principal_id, frozenset({"read", "write"}))},
        api_allow_anonymous=False,
    )
    return TestClient(create_app(cfg))


def _gql(client, query, variables=None):
    r = client.post(
        "/graphql",
        json={"query": query, "variables": variables or {}},
        headers={"Authorization": "Bearer test-token"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("errors") is None, body
    return body["data"]


def test_mutations_enqueue_and_jobs_query_lists_them(api_env):
    conn, doc_id, principal_id = api_env
    job_ids = []
    with _client(principal_id) as client:
        ann = _gql(
            client,
            "mutation($d: ID!) { annotateDocument(documentId: $d) { id kind status } }",
            {"d": doc_id},
        )["annotateDocument"]
        assert ann["kind"] == "annotate" and ann["status"] == "queued"
        job_ids.append(ann["id"])

        can = _gql(
            client,
            "mutation($d: ID!) { canonicalizeDocument(documentId: $d) { id kind } }",
            {"d": doc_id},
        )["canonicalizeDocument"]
        assert can["kind"] == "canonicalize"
        job_ids.append(can["id"])

        sem = _gql(
            client,
            "mutation($d: ID!) { semanticEnrichDocument(documentId: $d) { id kind } }",
            {"d": doc_id},
        )["semanticEnrichDocument"]
        assert sem["kind"] == "semantic_enrich"
        job_ids.append(sem["id"])

        listed = _gql(
            client,
            "query($d: ID!) { jobs(documentId: $d) { id kind status } }",
            {"d": doc_id},
        )["jobs"]
        assert {j["id"] for j in listed} >= set(job_ids)
        assert all(j["status"] == "queued" for j in listed if j["id"] in job_ids)
    for jid in job_ids:
        conn.execute("DELETE FROM jobs WHERE id = %s", (jid,))


def test_jobs_query_without_document_id_returns_recent_jobs(api_env):
    # Branch coverage: document_id is optional (the unscoped "recent jobs" view).
    _conn, _doc_id, principal_id = api_env
    with _client(principal_id) as client:
        data = _gql(client, "{ jobs(limit: 1) { id kind } }")
        assert isinstance(data["jobs"], list)


def test_job_queries_expose_queue_timing_and_attempts(api_env):
    conn, doc_id, principal_id = api_env
    queued_id = queue.enqueue(
        conn, "annotate", {"chain": []}, priority=-9_999, max_attempts=5, document_id=doc_id
    )
    finished_id = queue.enqueue(
        conn, "canonicalize", {"chain": []}, priority=-10_000, max_attempts=4, document_id=doc_id
    )
    claimed = queue.claim(conn, worker_id="api-pipeline-test")
    assert claimed is not None
    assert claimed.id == finished_id
    assert queue.complete(
        conn,
        finished_id,
        result={"ok": True},
        worker_id="api-pipeline-test",
    )
    try:
        with _client(principal_id) as client:
            finished = _gql(
                client,
                """
                query($id: ID!) {
                  job(id: $id) {
                    id createdAt startedAt finishedAt attempts maxAttempts
                  }
                }
                """,
                {"id": finished_id},
            )["job"]
            assert finished["id"] == finished_id
            assert finished["createdAt"] is not None
            assert finished["startedAt"] is not None
            assert finished["finishedAt"] is not None
            assert finished["attempts"] == 1
            assert finished["maxAttempts"] == 4

            listed = _gql(
                client,
                """
                query($documentId: ID!) {
                  jobs(documentId: $documentId) {
                    id createdAt startedAt finishedAt attempts maxAttempts
                  }
                }
                """,
                {"documentId": doc_id},
            )["jobs"]
            queued = next(job for job in listed if job["id"] == queued_id)
            assert queued["createdAt"] is not None
            assert queued["startedAt"] is None
            assert queued["finishedAt"] is None
            assert queued["attempts"] == 0
            assert queued["maxAttempts"] == 5
    finally:
        conn.execute("DELETE FROM jobs WHERE id IN (%s, %s)", (queued_id, finished_id))


def test_job_query_exposes_progress_fields(api_env):
    conn, doc_id, principal_id = api_env
    job_id = queue.enqueue(
        conn, "annotate", {"chain": []}, priority=-9_999, max_attempts=5, document_id=doc_id
    )
    claimed = queue.claim(conn, worker_id="api-progress-test")
    assert claimed is not None and claimed.id == job_id
    assert queue.update_progress(
        conn,
        job_id,
        current=4,
        total=12,
        stage="annotating sentence batch",
        worker_id="api-progress-test",
    )
    try:
        with _client(principal_id) as client:
            data = _gql(
                client,
                """
                query($id: ID!) {
                  job(id: $id) {
                    id progressCurrent progressTotal progressStage
                  }
                }
                """,
                {"id": job_id},
            )["job"]
        assert data == {
            "id": job_id,
            "progressCurrent": 4,
            "progressTotal": 12,
            "progressStage": "annotating sentence batch",
        }
    finally:
        conn.execute("DELETE FROM jobs WHERE id = %s", (job_id,))


def test_job_events_streams_visible_document_jobs(api_env):
    conn, doc_id, principal_id = api_env
    job_id = queue.enqueue(
        conn, "annotate", {"chain": []}, priority=-9_999, max_attempts=5, document_id=doc_id
    )
    try:
        with _client(principal_id) as client:
            response = client.get(
                f"/jobs/events?document_id={doc_id}&once=true",
                headers={"Authorization": "Bearer test-token"},
            )
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            body = response.text

        assert "event: job" in body
        assert f'"id":"{job_id}"' in body
        assert '"status":"queued"' in body
        assert '"kind":"annotate"' in body
    finally:
        conn.execute("DELETE FROM jobs WHERE id = %s", (job_id,))


def test_job_events_respect_document_rls(api_env):
    conn, doc_id, _principal_id = api_env
    job_id = queue.enqueue(
        conn, "annotate", {"chain": []}, priority=-9_999, max_attempts=5, document_id=doc_id
    )
    try:
        with _client(str(uuid4())) as client:
            response = client.get(
                f"/jobs/events?document_id={doc_id}&once=true",
                headers={"Authorization": "Bearer test-token"},
            )
        assert response.status_code == 200
        assert response.text == ""
    finally:
        conn.execute("DELETE FROM jobs WHERE id = %s", (job_id,))


def test_job_events_requires_api_authentication(api_env):
    _conn, doc_id, principal_id = api_env
    with _client(principal_id) as client:
        response = client.get(f"/jobs/events?document_id={doc_id}&once=true")
    assert response.status_code == 401


def test_jobs_query_with_invalid_document_id_returns_empty_not_an_error(api_env):
    # A non-UUID documentId must not hit the uuid-cast DB error path — it is a
    # clean, empty result (verified live incident: previously 500'd).
    _conn, _doc_id, principal_id = api_env
    with _client(principal_id) as client:
        data = _gql(client, '{ jobs(documentId: "not-a-uuid") { id } }')
        assert data["jobs"] == []


def test_full_text_search_query_finds_the_fixture_document(api_env):
    # Uses an invented word — a real one could rank outside the default result
    # page as the shared corpus grows (the exact bug fixed in
    # test_persistence_read.py's ts-config regression test).
    conn, doc_id, principal_id = api_env
    with _client(principal_id) as client:
        data = _gql(
            client,
            "query($q: String!) { fullTextSearch(query: $q) { documentId title } }",
            {"q": "zqorvithex"},
        )
        assert any(h["documentId"] == doc_id for h in data["fullTextSearch"])
