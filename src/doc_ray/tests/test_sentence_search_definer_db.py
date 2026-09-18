"""Sentence-text search must be able to use its index (BL-187).

**The defect.** `sentences.text` has carried a GIN trigram index since migration
002. Migration 018 put an RLS `p_read` policy on the table, and PostgreSQL will
not evaluate a non-leakproof qual before a security qual — `textlike` and
`texticlike` are both `proleakproof = false`. An index scan on `text` would test
the pattern before the permission check and could reveal that a hidden document
contains a search term, so the planner refuses. The index has therefore been
dead for every RLS-subject caller since migration 018, and `enable_seqscan = off`
does not help: the planner reports the sequential scan as the only option.

Measured on a corpus of ~2M sentences: an ordinary one-word search took several
seconds to return one page of 200, reading every sentence.

**The fix.** Candidate selection moves into a `SECURITY DEFINER` function owned
by the `BYPASSRLS` batch role, which applies the readable-document filter
explicitly in SQL. RLS is then not in the planner's way and the index is usable
again. That trades an automatic guarantee for one this module has to prove, so
the security tests come first here — they are the reason the performance test is
allowed to exist.
"""

from __future__ import annotations

import hashlib
import os
import uuid

import _itdb
import psycopg
import pytest
from psycopg.rows import dict_row

FUNCTION = "docray_readable_sentence_ids"
MATCH_ALL = "%"


def _conn():
    return _itdb.connect()


@pytest.fixture
def db():
    """A connection that removes everything the test created.

    An admin principal reads the entire tenant, so a document left behind by an
    earlier run is indistinguishable from one under test. Without teardown these
    assertions drift as the shared test database accumulates fixtures.
    """
    connection = _conn()
    created: dict[str, list[str]] = {"documents": [], "principals": []}
    connection.created = created  # type: ignore[attr-defined]
    try:
        yield connection
    finally:
        for doc in created["documents"]:
            connection.execute("DELETE FROM documents WHERE id = %s", (doc,))
        for principal in created["principals"]:
            connection.execute("DELETE FROM principal_roles WHERE principal_id = %s", (principal,))
            connection.execute("DELETE FROM principals WHERE id = %s", (principal,))
        connection.close()


def _make_doc(conn, title: str, *, owner: str | None, sentences: list[str]) -> str:
    doc = conn.execute(
        "INSERT INTO documents (title, doc_type, source_uri, content_sha256, byte_size, "
        "owner_principal_id, visibility) VALUES (%s,'txt','s3://t',%s,1,%s,'private') "
        "RETURNING id",
        (title, hashlib.sha256(os.urandom(16)).hexdigest(), owner),
    ).fetchone()["id"]
    conn.created["documents"].append(doc)
    span = conn.execute(
        "INSERT INTO spans (document_id, char_start, char_end) VALUES (%s,0,1) RETURNING id",
        (doc,),
    ).fetchone()["id"]
    from doc_ray.domain.ulid import new_ulid_str

    for ordinal, text in enumerate(sentences):
        conn.execute(
            "INSERT INTO sentences (id, document_id, ordinal, text, span_id) "
            "VALUES (%s,%s,%s,%s,%s)",
            (new_ulid_str(), doc, ordinal, text, span),
        )
    return str(doc)


def _principal(conn, *, admin: bool = False) -> str:
    pid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO principals (id, kind, display_name) VALUES (%s,'service',%s)",
        (pid, f"defnr-{pid[:8]}"),
    )
    conn.created["principals"].append(pid)
    if admin:
        role = conn.execute("SELECT id FROM roles WHERE name='admin'").fetchone()["id"]
        conn.execute(
            "INSERT INTO principal_roles (principal_id, role_id) VALUES (%s,%s) "
            "ON CONFLICT DO NOTHING",
            (pid, role),
        )
    return pid


def _as_caller(principal_id: str):
    """A connection scoped exactly as the API scopes one: role + principal GUC."""
    conn = psycopg.connect(_itdb.DB_URL, row_factory=dict_row, autocommit=True)
    conn.execute("SET ROLE docray_api")
    conn.execute("SELECT set_config('app.principal_id', %s, false)", (principal_id,))
    return conn


def _call(conn, *patterns: str, doc: str | None = None, cap: int = 100):
    padded = list(patterns) + [MATCH_ALL] * (4 - len(patterns))
    return conn.execute(
        f"SELECT * FROM {FUNCTION}(%s,%s,%s,%s,%s::uuid,%s)",
        (*padded, doc, cap),
    ).fetchall()


# --- the function exists with the shape the security argument depends on -----


def test_the_function_is_security_definer(db):
    row = db.execute("SELECT prosecdef FROM pg_proc WHERE proname = %s", (FUNCTION,)).fetchone()

    assert row is not None, f"{FUNCTION} does not exist"
    assert row["prosecdef"] is True


def test_the_function_is_owned_by_a_bypassrls_role(db):
    """Migration 114's invariant: a definer function reading a forced-RLS table
    must be BYPASSRLS-owned, or it recurses into the policy it is resolving."""
    row = db.execute(
        "SELECT r.rolname, r.rolbypassrls FROM pg_proc p JOIN pg_roles r ON r.oid = p.proowner "
        "WHERE p.proname = %s",
        (FUNCTION,),
    ).fetchone()

    assert row["rolbypassrls"] is True, f"{FUNCTION} owned by non-BYPASSRLS {row['rolname']}"


def test_the_function_pins_its_search_path(db):
    """An unpinned search_path on a definer function is a privilege-escalation route."""
    row = db.execute("SELECT proconfig FROM pg_proc WHERE proname = %s", (FUNCTION,)).fetchone()

    assert row["proconfig"], "search_path is not pinned"
    assert any(c.startswith("search_path=") for c in row["proconfig"])


# --- security: the caller's permissions, not the definer's -------------------


def test_it_returns_only_sentences_the_caller_may_read(db):
    """The whole safety case. Bypassing RLS means this filter is the only one left."""
    mine = _principal(db)
    theirs = _principal(db)
    _make_doc(db, "mine", owner=mine, sentences=["the quokka manifesto is here"])
    _make_doc(db, "theirs", owner=theirs, sentences=["the quokka manifesto is hidden"])

    conn = _as_caller(mine)
    try:
        rows = _call(conn, "%quokka%")
    finally:
        conn.close()

    assert len(rows) == 1, "the caller saw a sentence from a document they do not own"


def test_a_caller_with_no_readable_documents_gets_nothing(db):
    stranger = _principal(db)
    owner = _principal(db)
    _make_doc(db, "owned", owner=owner, sentences=["a wombat treatise"])

    conn = _as_caller(stranger)
    try:
        rows = _call(conn, "%wombat%")
    finally:
        conn.close()

    assert rows == []


def test_an_admin_sees_documents_they_do_not_own(db):
    """Admins read the whole tenant; the function must not narrow that."""
    admin = _principal(db, admin=True)
    other = _principal(db)
    _make_doc(db, "other", owner=other, sentences=["a numbat compendium"])

    conn = _as_caller(admin)
    try:
        rows = _call(conn, "%numbat%")
    finally:
        conn.close()

    assert len(rows) == 1


def test_the_definers_own_identity_is_never_used_as_the_caller(db):
    """`SECURITY DEFINER` changes `current_user` but not the `role` GUC.

    If the permission filter resolved the *definer's* principal, every caller
    would inherit the batch role's visibility. Pinned explicitly because the
    entire design rests on it.
    """
    owner = _principal(db)
    _make_doc(db, "owned", owner=owner, sentences=["a bilby anthology"])
    stranger = _principal(db)

    conn = _as_caller(stranger)
    try:
        assert _call(conn, "%bilby%") == []
    finally:
        conn.close()

    conn = _as_caller(owner)
    try:
        assert len(_call(conn, "%bilby%")) == 1
    finally:
        conn.close()


# --- behaviour: it is a candidate superset, bounded and filterable -----------


def test_every_supplied_pattern_narrows_the_result(db):
    owner = _principal(db)
    _make_doc(
        db,
        "multi",
        owner=owner,
        sentences=["alpha beta gamma here", "alpha only here", "beta only here"],
    )

    conn = _as_caller(owner)
    try:
        both = _call(conn, "%alpha%", "%beta%")
        one = _call(conn, "%alpha%")
    finally:
        conn.close()

    assert len(both) == 1
    assert len(one) == 2


def test_the_match_all_pad_does_not_narrow_anything(db):
    """Fixed arity is padded with `%`; padding must be a no-op."""
    owner = _principal(db)
    _make_doc(db, "pad", owner=owner, sentences=["padding check sentence"])

    conn = _as_caller(owner)
    try:
        assert len(_call(conn, "%padding%")) == 1
    finally:
        conn.close()


def test_a_document_filter_restricts_to_that_document(db):
    owner = _principal(db)
    first = _make_doc(db, "first", owner=owner, sentences=["a shared echidna line"])
    _make_doc(db, "second", owner=owner, sentences=["another shared echidna line"])

    conn = _as_caller(owner)
    try:
        assert len(_call(conn, "%echidna%")) == 2
        assert len(_call(conn, "%echidna%", doc=first)) == 1
    finally:
        conn.close()


def test_the_cap_bounds_the_result(db):
    owner = _principal(db)
    _make_doc(db, "many", owner=owner, sentences=[f"capybara number {i}" for i in range(20)])

    conn = _as_caller(owner)
    try:
        assert len(_call(conn, "%capybara%", cap=5)) == 5
    finally:
        conn.close()


def test_matching_is_case_insensitive_like_the_shipped_clause(db):
    owner = _principal(db)
    _make_doc(db, "case", owner=owner, sentences=["A Platypus Monograph"])

    conn = _as_caller(owner)
    try:
        assert len(_call(conn, "%platypus%")) == 1
    finally:
        conn.close()
