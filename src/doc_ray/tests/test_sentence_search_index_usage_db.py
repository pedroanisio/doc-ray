"""The regression that matters: search must actually reach its index (BL-187).

Every other test in this change can pass while search is still doing a full
sequential scan of the corpus, because correctness and speed are independent
here. This module asserts the *plan*, which is the only thing that distinguishes
the fixed code from the broken code.

The discriminator is `enable_seqscan = off`. On a small fixture table the
planner prefers a sequential scan on cost alone, which says nothing. Turning it
off forces the question the defect is about: *is an index scan available at all?*
Under RLS the answer was no — PostgreSQL returned the sequential scan anyway,
with the 1e10 disable penalty in its cost, because a non-leakproof `ILIKE` may
not be evaluated ahead of a security qual.
"""

from __future__ import annotations

import hashlib
import os
import uuid

import _itdb
import psycopg
import pytest
from psycopg.rows import dict_row

from doc_ray.domain.ulid import new_ulid_str

TRIGRAM_INDEX = "idx_sentences_text_trgm"
#: pg_trgm cannot extract a trigram from fewer than three characters.
TRIGRAM_MIN_CHARS = 3
#: Deliberately rare: a term matching most rows is one the planner *should*
#: seq-scan for, which would make these assertions meaningless.
PROBE_TERM = "quokkazephyr"
#: Large enough that a sequential scan is genuinely the more expensive plan.
#: Below roughly ten thousand rows the planner prefers a scan whatever the
#: index can do, and a cost-based assertion would be measuring the fixture.
CORPUS_ROWS = 40_000


@pytest.fixture(scope="module")
def seeded():
    """A corpus big enough that an index scan is a meaningful alternative."""
    conn = _itdb.connect()
    principal = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO principals (id, kind, display_name) VALUES (%s,'service',%s)",
        (principal, f"idx-{principal[:8]}"),
    )
    doc = conn.execute(
        "INSERT INTO documents (title, doc_type, source_uri, content_sha256, byte_size, "
        "owner_principal_id, visibility) VALUES ('index-usage','txt','s3://idx',%s,1,%s,'private') "
        "RETURNING id",
        (hashlib.sha256(os.urandom(16)).hexdigest(), principal),
    ).fetchone()["id"]
    span = conn.execute(
        "INSERT INTO spans (document_id, char_start, char_end) VALUES (%s,0,1) RETURNING id",
        (doc,),
    ).fetchone()["id"]
    # The probe term must be *rare*: a pattern matching most rows is one the
    # planner should reject the index for, so a non-selective fixture would
    # prove nothing about whether the index is reachable.
    words = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta", "iota", "kappa"]
    rows = [
        (new_ulid_str(), doc, i, " ".join(words[(i + j) % len(words)] for j in range(8)), span)
        for i in range(CORPUS_ROWS)
    ]
    rows[4242] = (new_ulid_str(), doc, 4242, f"a solitary {PROBE_TERM} appears here", span)
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO sentences (id, document_id, ordinal, text, span_id) "
            "VALUES (%s,%s,%s,%s,%s)",
            rows,
        )
    conn.execute("ANALYZE sentences")
    try:
        yield {"principal": principal, "doc": str(doc)}
    finally:
        conn.execute("DELETE FROM documents WHERE id = %s", (doc,))
        conn.execute("DELETE FROM principals WHERE id = %s", (principal,))
        conn.close()


def _scan_node(plan_rows) -> str:
    return next((r["QUERY PLAN"].strip() for r in plan_rows if "Scan" in r["QUERY PLAN"]), "")


def _as_batch():
    """A connection running as the definer function's own owner."""
    conn = psycopg.connect(_itdb.DB_URL, row_factory=dict_row, autocommit=True)
    conn.execute("SET ROLE docray_batch")
    return conn


def _as_api(principal: str):
    conn = psycopg.connect(_itdb.DB_URL, row_factory=dict_row, autocommit=True)
    conn.execute("SET ROLE docray_api")
    conn.execute("SELECT set_config('app.principal_id', %s, false)", (principal,))
    return conn


#: What the adapters issue today: no explicit document predicate, because RLS
#: supplies its own. This is the shape that cannot reach the index.
RLS_CANDIDATE_SQL = (
    "SELECT s.id FROM sentences s "
    "WHERE s.text ILIKE %(p1)s AND s.text ILIKE %(p2)s "
    "AND s.text ILIKE %(p3)s AND s.text ILIKE %(p4)s LIMIT 200"
)
#: What the definer function's body does: the permission filter written out, so
#: the planner is free to choose among the quals rather than being ordered.
DEFINER_CANDIDATE_SQL = (
    "SELECT s.id FROM sentences s "
    "WHERE s.document_id IN (SELECT docray_readable_documents()) "
    "AND s.text ILIKE %(p1)s AND s.text ILIKE %(p2)s "
    "AND s.text ILIKE %(p3)s AND s.text ILIKE %(p4)s LIMIT 200"
)
PATTERNS = {"p1": f"%{PROBE_TERM}%", "p2": "%", "p3": "%", "p4": "%"}


def test_the_trigram_index_exists(seeded):
    """If this ever fails, every assertion below is meaningless."""
    conn = _itdb.connect()
    try:
        row = conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE tablename='sentences' AND indexname=%s",
            (TRIGRAM_INDEX,),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert "gin_trgm_ops" in row["indexdef"]


def test_under_rls_the_index_is_unavailable_even_when_seqscan_is_disabled(seeded):
    """Guard the guard: this is the defect, and it must still reproduce.

    If this test starts failing, the leakproof interaction has changed and the
    whole justification for the definer function needs re-deriving — do not
    simply delete it.
    """
    conn = _as_api(seeded["principal"])
    try:
        conn.execute("SET enable_seqscan = off")
        node = _scan_node(conn.execute("EXPLAIN " + RLS_CANDIDATE_SQL, PATTERNS).fetchall())
    finally:
        conn.close()

    assert "Seq Scan" in node, f"expected the RLS path to be stuck on a seq scan, got {node!r}"


def test_the_definer_context_can_reach_the_trigram_index(seeded):
    """The fix, stated as a plan rather than as a timing."""
    conn = _as_batch()
    try:
        conn.execute("SET enable_seqscan = off")
        node = _scan_node(conn.execute("EXPLAIN " + DEFINER_CANDIDATE_SQL, PATTERNS).fetchall())
    finally:
        conn.close()

    assert "Seq Scan" not in node, f"still sequential in the definer context: {node!r}"
    assert "Bitmap" in node or "Index" in node


def test_padding_terms_do_not_prevent_the_index_from_being_used(seeded):
    """Fixed arity is only viable if the `%` pads keep the index reachable.

    A `%` pattern matches everything and yields no trigram, so it should be a
    filter rather than something that forces a scan. Asserted on reachability
    for the same reason as the test above.
    """
    conn = _as_batch()
    try:
        conn.execute("SET enable_seqscan = off")
        one = _scan_node(conn.execute("EXPLAIN " + DEFINER_CANDIDATE_SQL, PATTERNS).fetchall())
        many = _scan_node(
            conn.execute(
                "EXPLAIN " + DEFINER_CANDIDATE_SQL,
                {"p1": f"%{PROBE_TERM}%", "p2": "%solitary%", "p3": "%appears%", "p4": "%"},
            ).fetchall()
        )
    finally:
        conn.close()

    assert "Seq Scan" not in one, f"one real pattern + pads fell back to a scan: {one!r}"
    assert "Seq Scan" not in many, f"three real patterns fell back to a scan: {many!r}"


def test_cost_based_choice_is_not_asserted_here_and_why(seeded):
    """Whether the planner *prefers* the index is not provable in this fixture.

    Two reasons, both about the fixture rather than the fix:

    1. Cost-based choice is a function of corpus scale. These rows are short and
       uniform, so a sequential scan over them is genuinely cheap and the
       planner is right to take it. Production is 1,966,114 sentences / 1,277 MB.
    2. Plans here are taken as `docray_batch`, which has no principal and
       therefore no readable documents, so the permission filter matches nothing
       and the join short-circuits — the shape gets measured, not the cost.

    What is provable here is *reachability*, which is exactly what the defect
    was about: RLS made the index unavailable at any cost. The preference is
    measured at production scale by `scripts/eval_phrase_retrieval.py`, whose
    report records the chosen access path per query class.
    """
    conn = _as_batch()
    try:
        readable = conn.execute("SELECT count(*) AS n FROM docray_readable_documents()").fetchone()[
            "n"
        ]
    finally:
        conn.close()

    assert readable == 0, (
        "docray_batch now resolves readable documents, so the reasoning above no "
        "longer holds and a cost-based assertion may have become meaningful"
    )


def test_a_term_too_short_for_a_trigram_is_documented_as_a_scan(seeded):
    """`of`, `it` cannot be indexed — pg_trgm needs three characters.

    Recorded rather than worked around: this is the one class the fix cannot
    help, and it is already the fast case because it fills the candidate cap
    immediately.
    """
    assert len("of") < TRIGRAM_MIN_CHARS

    conn = _as_batch()
    try:
        node = _scan_node(
            conn.execute(
                "EXPLAIN " + DEFINER_CANDIDATE_SQL,
                {"p1": "%of%", "p2": "%", "p3": "%", "p4": "%"},
            ).fetchall()
        )
    finally:
        conn.close()

    assert "Seq Scan" in node
