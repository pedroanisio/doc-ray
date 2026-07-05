"""Seed-suggestion workflow tests: mine -> resolve -> persist -> decide (skip-clean).

The staging area that makes domain seeding webapp-curable: a job mines candidate
terms from a document (unlinked mention surfaces + frequent noun lemmas), resolves
them to QIDs via Wikipedia pageprops, and persists rows a human approves/rejects
before any canonical is minted. Stub resolvers — no network in tests.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from doc_ray.domain.ulid import new_ulid_str

_TEXT = (
    "The flombard press printed a flombard daily. Krellith ran the flombard show. "
    "A vantiquark hummed; the vantiquark glowed; every vantiquark sings twice more."
)


def _connect_or_skip():
    import _itdb

    try:
        return _itdb.connect()
    except Exception as exc:  # pragma: no cover - env dependent
        pytest.skip(f"DB unreachable: {exc}")


@pytest.fixture
def sg_env():
    import _itdb

    conn = _connect_or_skip()
    doc_id = _itdb.make_doc(conn, _TEXT)
    ann = new_ulid_str()
    conn.execute(
        "INSERT INTO annotations (id, document_id, layer, tool) VALUES (%s,%s,'spacy','test')",
        (ann, doc_id),
    )

    def add_span(start, end):
        return conn.execute(
            "INSERT INTO spans (document_id, char_start, char_end) VALUES (%s,%s,%s) "
            "ON CONFLICT (document_id, char_start, char_end) DO UPDATE SET char_start=%s "
            "RETURNING id",
            (doc_id, start, end, start),
        ).fetchone()["id"]

    import re

    def offsets(word):
        return [(m.start(), m.end()) for m in re.finditer(word, _TEXT)]

    # three 'flombard' mentions + one DATE mention ('daily'); offsets derived, not hand-counted
    for start, end in offsets("flombard"):
        conn.execute(
            "INSERT INTO entity_mention (id, annotation_id, span_id, entity_type, surface) "
            "VALUES (%s,%s,%s,'ORG',%s)",
            (new_ulid_str(), ann, add_span(start, end), _TEXT[start:end]),
        )
    ((dstart, dend),) = offsets("daily")
    conn.execute(
        "INSERT INTO entity_mention (id, annotation_id, span_id, entity_type, surface) "
        "VALUES (%s,%s,%s,'DATE',%s)",
        (new_ulid_str(), ann, add_span(dstart, dend), _TEXT[dstart:dend]),
    )
    # noun lexeme 'vantiquark' with three occurrences
    conn.execute(
        "INSERT INTO lexemes (uid, lang, pos, lemma) VALUES ('t:vantiquark','en','NOUN','vantiquark') "
        "ON CONFLICT (uid) DO NOTHING"
    )
    for start, end in offsets("vantiquark"):
        conn.execute(
            "INSERT INTO lexeme_occurrences (annotation_id, span_id, lexeme_uid) "
            "VALUES (%s,%s,'t:vantiquark') ON CONFLICT DO NOTHING",
            (ann, add_span(start, end)),
        )
    try:
        yield conn, doc_id
    finally:
        conn.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
        conn.execute("DELETE FROM lexemes WHERE uid = 't:vantiquark'")
        conn.close()


def test_mine_candidates_finds_mentions_and_noun_lemmas(sg_env):
    from doc_ray.application.seed_suggest import mine_candidates

    conn, doc_id = sg_env
    cands = mine_candidates(conn, doc_id, min_mentions=2, min_lexemes=2)
    by_term = {c.term: c for c in cands}
    assert by_term["flombard"].source == "mention"
    assert by_term["flombard"].occurrences == 3
    assert by_term["vantiquark"].source == "lexeme"
    assert by_term["vantiquark"].occurrences == 3
    assert "daily" not in by_term  # non-referential mention types are excluded


class _StubWiki:
    def __init__(self, mapping):
        self.mapping = mapping

    def qids_for_titles(self, titles):
        return {t: self.mapping.get(t) for t in titles}


class _StubEntry:
    def __init__(self, kb_id, label, description, entity_type):
        self.kb_id, self.label = kb_id, label
        self.description, self.entity_type = description, entity_type
        self.aliases, self.lang = [], "en"


class _StubKb:
    def __init__(self, entries):
        self.entries = entries

    def get(self, qid):
        return self.entries.get(qid)


def test_suggest_for_document_persists_resolved_and_unresolved(sg_env):
    from doc_ray.adapters import seed_suggestion_store as sstore
    from doc_ray.application.seed_suggest import suggest_for_document

    conn, doc_id = sg_env
    wiki = _StubWiki({"flombard": "Q900001"})  # vantiquark: no article
    kb = _StubKb({"Q900001": _StubEntry("Q900001", "Flombard", "fictive press", "ORG")})
    out = suggest_for_document(conn, wiki, kb, doc_id, min_mentions=2, min_lexemes=2)
    assert out == {"suggested": 2, "resolved": 1}

    rows = sstore.list_for_document(conn, doc_id)
    by_term = {r["term"]: r for r in rows}
    assert by_term["flombard"]["qid"] == "Q900001"
    assert by_term["flombard"]["description"] == "fictive press"
    assert by_term["flombard"]["status"] == "pending"
    assert by_term["vantiquark"]["qid"] is None  # kept, visible as unresolved

    # re-run is idempotent: same rows, no duplicates
    out2 = suggest_for_document(conn, wiki, kb, doc_id, min_mentions=2, min_lexemes=2)
    assert out2["suggested"] == 2
    assert len(sstore.list_for_document(conn, doc_id)) == 2


def test_decide_and_approved_qids_roundtrip(sg_env):
    from doc_ray.adapters import seed_suggestion_store as sstore
    from doc_ray.application.seed_suggest import suggest_for_document

    conn, doc_id = sg_env
    wiki = _StubWiki({"flombard": "Q900001", "vantiquark": "Q900002"})
    kb = _StubKb(
        {
            "Q900001": _StubEntry("Q900001", "Flombard", "fictive press", "ORG"),
            "Q900002": _StubEntry("Q900002", "Vantiquark", "fictive particle", "CONCEPT"),
        }
    )
    suggest_for_document(conn, wiki, kb, doc_id, min_mentions=2, min_lexemes=2)
    rows = sstore.list_for_document(conn, doc_id)
    ids = {r["term"]: str(r["id"]) for r in rows}

    n = sstore.decide(conn, [ids["flombard"]], "approved")
    n += sstore.decide(conn, [ids["vantiquark"]], "rejected")
    assert n == 2
    assert sstore.approved_qids(conn, doc_id) == ["Q900001"]

    sstore.mark_seeded(conn, doc_id)
    statuses = {r["term"]: r["status"] for r in sstore.list_for_document(conn, doc_id)}
    assert statuses == {"flombard": "seeded", "vantiquark": "rejected"}


def test_seed_approved_job_seeds_and_finalizes(sg_env, monkeypatch):
    # The worker closes the loop: read this document's approved QIDs, seed them
    # through the ordinary canonical_seed path, and mark the rows 'seeded'.
    from doc_ray.adapters import queue_postgres as q
    from doc_ray.adapters import seed_suggestion_store as sstore
    from doc_ray.application import jobs as jobs_mod
    from doc_ray.domain.jobs import JobStatus

    conn, doc_id = sg_env
    sid = new_ulid_str()
    conn.execute(
        "INSERT INTO seed_suggestion (id, document_id, term, occurrences, qid, label, status) "
        "VALUES (%s,%s,'flombard',3,'Q900001','Flombard','approved')",
        (sid, doc_id),
    )

    seeded_qids: list[list[str]] = []

    class _NoNetKb:
        def close(self):
            return None

    monkeypatch.setattr("doc_ray.adapters.wikidata.WikidataClient", lambda *a, **k: _NoNetKb())
    monkeypatch.setattr(
        "doc_ray.application.canonical_seed.seed_from_ids",
        lambda _c, _kb, ids: seeded_qids.append(list(ids)) or [1] * len(ids),
    )

    job_id = q.enqueue(conn, "seed_approved", {}, priority=0, document_id=doc_id)
    try:
        done = jobs_mod.run_once(conn, None, worker_id="w-test", handlers=jobs_mod.HANDLERS)
        assert done is not None and done.id == job_id
        assert q.get(conn, job_id).status is JobStatus.SUCCEEDED
        assert seeded_qids == [["Q900001"]]
        assert sstore.list_for_document(conn, doc_id)[0]["status"] == "seeded"
    finally:
        conn.execute("DELETE FROM jobs WHERE id = %s", (job_id,))


def test_graphql_surface_covers_the_curation_loop(sg_env):
    # The webapp's contract: list suggestions, decide them, enqueue the seed job
    # — all through the GraphQL API.
    from fastapi.testclient import TestClient

    from doc_ray.config import Config
    from doc_ray.entrypoints.api.app import create_app

    conn, doc_id = sg_env
    principal = conn.execute(
        "SELECT id FROM principals WHERE display_name = 'bootstrap-admin'"
    ).fetchone()
    if principal is None:
        pytest.skip("RBAC seed principal absent")
    sid = new_ulid_str()
    conn.execute(
        "INSERT INTO seed_suggestion "
        "  (id, document_id, term, occurrences, qid, label, description, status) "
        "VALUES (%s,%s,'flombard',3,'Q900001','Flombard','fictive press','pending')",
        (sid, doc_id),
    )
    rid = new_ulid_str()
    conn.execute(
        "INSERT INTO seed_suggestion "
        "  (id, document_id, term, occurrences, qid, label, description, status) "
        "VALUES (%s,%s,'noisyterm',1,'Q900099','Noisy','junk match','pending')",
        (rid, doc_id),
    )

    cfg = replace(
        Config.from_env(),
        api_tokens={"test-token": (str(principal["id"]), frozenset({"read", "write"}))},
        api_allow_anonymous=False,
    )
    app = create_app(cfg)
    with TestClient(app) as client:

        def gql(query, variables=None):
            r = client.post(
                "/graphql",
                json={"query": query, "variables": variables or {}},
                headers={"Authorization": "Bearer test-token"},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body.get("errors") is None, body
            return body["data"]

        listed = gql(
            "query($d: ID!) { seedSuggestions(documentId: $d) { id term qid description status } }",
            {"d": doc_id},
        )["seedSuggestions"]
        assert {s["term"] for s in listed} == {"flombard", "noisyterm"}
        by_term = {s["term"]: s for s in listed}
        assert by_term["flombard"]["description"] == "fictive press"

        n = gql(
            "mutation($a: [ID!], $r: [ID!]) { decideSeeds(approveIds: $a, rejectIds: $r) }",
            {"a": [sid], "r": [rid]},
        )["decideSeeds"]
        assert n == 2  # both the approve and the reject branch ran

        rejected = conn.execute(
            "SELECT status FROM seed_suggestion WHERE id = %s", (rid,)
        ).fetchone()
        assert rejected["status"] == "rejected"

        job = gql(
            "mutation($d: ID!) { seedApproved(documentId: $d) { id kind status } }",
            {"d": doc_id},
        )["seedApproved"]
        assert job["kind"] == "seed_approved"
        conn.execute("DELETE FROM jobs WHERE id = %s", (job["id"],))

    row = conn.execute("SELECT status FROM seed_suggestion WHERE id = %s", (sid,)).fetchone()
    assert row["status"] == "approved"


def test_decide_never_touches_seeded_rows(sg_env):
    from doc_ray.adapters import seed_suggestion_store as sstore

    conn, doc_id = sg_env
    sid = new_ulid_str()
    conn.execute(
        "INSERT INTO seed_suggestion (id, document_id, term, occurrences, qid, status) "
        "VALUES (%s,%s,'donetta',5,'Q900009','seeded')",
        (sid, doc_id),
    )
    assert sstore.decide(conn, [sid], "rejected") == 0  # seeded is final
    row = sstore.list_for_document(conn, doc_id)[0]
    assert row["status"] == "seeded"
