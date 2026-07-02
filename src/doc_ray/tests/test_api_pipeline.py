"""GraphQL pipeline surface: jobs query + annotate/canonicalize mutations (skip-clean).

The webapp's upload flow contract: after ingestDocument, a client renders
pipeline progress from ``jobs(documentId:)`` and can trigger the individual
steps explicitly. All writes enqueue worker jobs — nothing runs in-request.
"""

from __future__ import annotations

import pytest


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
    doc_id = _itdb.make_doc(conn, "Pipeline surface test. Second sentence.")
    try:
        yield conn, doc_id
    finally:
        conn.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
        conn.close()


def _client():
    from fastapi.testclient import TestClient

    from doc_ray.config import Config
    from doc_ray.entrypoints.api.app import create_app

    return TestClient(create_app(Config.from_env()))


def _gql(client, query, variables=None):
    r = client.post("/graphql", json={"query": query, "variables": variables or {}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("errors") is None, body
    return body["data"]


def test_mutations_enqueue_and_jobs_query_lists_them(api_env):
    conn, doc_id = api_env
    job_ids = []
    with _client() as client:
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

        listed = _gql(
            client,
            "query($d: ID!) { jobs(documentId: $d) { id kind status } }",
            {"d": doc_id},
        )["jobs"]
        assert {j["id"] for j in listed} >= set(job_ids)
        assert all(j["status"] == "queued" for j in listed if j["id"] in job_ids)
    for jid in job_ids:
        conn.execute("DELETE FROM jobs WHERE id = %s", (jid,))
