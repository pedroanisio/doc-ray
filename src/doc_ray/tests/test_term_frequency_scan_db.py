"""Integration + RLS tests for the bounded sentence scan (#39; skip without a DB).

What only a real database can prove: the count is the tenant's actual matching
sentences, the whole batch is ONE statement, the cap truly bounds the scan, the
predicate is ``match_clause`` (so the verdict predicts ``search_sentences``), the
denominator comes from the ``corpus_stats`` rollup rather than a fact-table scan,
and another tenant's sentences are never counted.

The pure band logic is covered by test_term_frequency_scan.py; the
``corpus_stats.n_sentences`` rollup arithmetic (migration 142) lives in
test_corpus_stats_rollups.py, which owns the schema replay those assertions need.
"""

from __future__ import annotations

import asyncio

from doc_ray.adapters import persistence_read_terms as terms_read
from doc_ray.domain import term_frequency as tf
from doc_ray.tests._mutations_common import _aconn_as

ADMIN_PID = "00000000-0000-0000-0000-0000000000f7"

#: Distinctive so no other suite's fixture can contribute a hit.
_PHRASE = "quxscan phrase"
_ABSENT = "quxscan nowhere"


def _itdb():
    """The integration-DB helpers (imported lazily, as the suite does)."""
    import _itdb as helpers

    return helpers


def _seed_sentences(pg, texts: list[str]) -> str:
    """Seed one document whose sentences are *texts*, through the production path.

    ``make_doc`` runs the real sentence splitter and refreshes both rollups, so the
    fixture exercises the same shape production writes — each element must
    therefore be a single sentence ending in a full stop.
    """
    import _itdb

    conn, created = pg
    _itdb.ensure_admin_principal(conn, ADMIN_PID, "test-sentence-scan")
    doc_id = _itdb.make_doc(conn, " ".join(texts))
    created.append(doc_id)
    return doc_id


def _scan(keys_to_text: dict[str, str], *, cap: int = tf.SENTENCE_HIGH_HITS):
    """Run the adapter as the seeded admin; return (scans, total_sentences)."""
    out: dict = {}

    async def check():
        aconn = await _aconn_as(ADMIN_PID)
        try:
            out["result"] = await terms_read.sentence_hit_counts(aconn, keys_to_text, cap=cap)
        finally:
            await aconn.close()

    asyncio.run(check())
    return out["result"]


# --- TEST-A1: real counts, one statement ------------------------------------


def test_the_scan_counts_the_tenants_matching_sentences(pg):
    _seed_sentences(
        pg,
        [
            f"A sentence about the {_PHRASE} here.",
            f"Another mentioning {_PHRASE} again.",
            "An unrelated sentence entirely.",
        ],
    )

    scans, total_sentences = _scan({"a": _PHRASE, "b": _ABSENT})

    assert scans["a"].hits == 2
    assert scans["a"].capped is False
    # Zero is a real measurement here — the string was searched and not found.
    assert scans["b"].hits == 0
    assert scans["b"].capped is False
    assert total_sentences >= 3


def test_a_batch_of_three_terms_is_served_by_a_single_sql_statement(pg, monkeypatch):
    """The house rule: one statement per batch, never N queries in a loop."""
    _seed_sentences(pg, [f"Only one {_PHRASE} sentence."])

    statements: list[str] = []
    original = terms_read._rows

    async def _spy(conn, sql, params):
        statements.append(sql)
        return await original(conn, sql, params)

    monkeypatch.setattr(terms_read, "_rows", _spy)
    scans, _total = _scan({"a": _PHRASE, "b": _ABSENT, "c": "quxscan third"})

    assert len(scans) == 3
    assert len(statements) == 1, f"expected one statement, issued {len(statements)}"


# --- TEST-A2: the cap bounds the scan ---------------------------------------


def test_the_cap_bounds_the_count_and_reports_a_floor(pg):
    """With 5 matches and cap=3 the answer is 3 AND says it is not the total."""
    _seed_sentences(pg, [f"Match {i} of {_PHRASE} text." for i in range(5)])

    scans, _total = _scan({"a": _PHRASE}, cap=3)

    assert scans["a"].hits == 3
    assert scans["a"].capped is True


def test_an_uncapped_count_is_reported_as_exact(pg):
    _seed_sentences(pg, [f"Match {i} of {_PHRASE} text." for i in range(2)])

    scans, _total = _scan({"a": _PHRASE}, cap=10)

    assert scans["a"].hits == 2
    assert scans["a"].capped is False


# --- TEST-A3: the predicate is match_clause, not an inlined ILIKE ------------


def test_a_typed_apostrophe_matches_the_typographic_one(pg):
    """Proves the shared rule is the predicate: only match_clause folds ’ to '.

    If this adapter inlined its own ILIKE, a caller typing ASCII ``'`` would miss
    every sentence the extractor stored with ``’`` — and the pre-flight would
    disagree with the search tool it exists to predict.
    """
    _seed_sentences(pg, ["The quxscan Emperor’s decree was read aloud."])

    scans, _total = _scan({"a": "quxscan Emperor's"})

    assert scans["a"].hits == 1


def test_wildcards_in_the_term_are_data_not_syntax(pg):
    """``%`` must not turn into a match-everything pattern."""
    _seed_sentences(pg, ["A plain quxscan sentence.", "Another quxscan one."])

    scans, _total = _scan({"a": "quxscan%"})

    assert scans["a"].hits == 0


# --- TEST-A4 / A5: the denominator and RLS ----------------------------------


def test_total_sentences_comes_from_the_corpus_stats_rollup(pg):
    """D-024: the denominator is one rollup scalar, never a count(*) over sentences."""
    conn, _created = pg
    doc_id = _seed_sentences(pg, [f"Rollup {i} {_PHRASE}." for i in range(4)])

    _scans, total_sentences = _scan({"a": _PHRASE})
    # The adapter's denominator is RLS-scoped to the caller's tenant, so compare it
    # against that tenant's rollup row — not a sum across every tenant, which other
    # suites' fixtures also write to.
    rollup = conn.execute(
        "SELECT COALESCE(n_sentences, 0) AS n FROM corpus_stats WHERE tenant_id = "
        "(SELECT tenant_id FROM documents WHERE id = %s)",
        (doc_id,),
    ).fetchone()["n"]

    assert total_sentences == int(rollup)
    assert total_sentences > 0


def test_a_caller_in_another_tenant_counts_none_of_our_sentences(pg):
    """RLS scopes the scan; the adapter adds no tenant filter of its own."""
    _seed_sentences(pg, [f"Private {_PHRASE} sentence."])

    async def check():
        aconn = await _aconn_as("11111111-1111-1111-1111-1111111111f7")
        try:
            return await terms_read.sentence_hit_counts(aconn, {"a": _PHRASE}, cap=100)
        finally:
            await aconn.close()

    scans, total_sentences = asyncio.run(check())

    assert scans["a"].hits == 0
    assert total_sentences == 0


def test_an_empty_request_costs_no_query(pg):
    scans, total_sentences = _scan({})

    assert scans == {}
    assert total_sentences == 0
