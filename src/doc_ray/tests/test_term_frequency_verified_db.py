"""Database and GraphQL contracts for verified phrase-frequency counts (#46)."""

from __future__ import annotations

import asyncio

from doc_ray.adapters import persistence_read_terms as terms_read
from doc_ray.domain import term_frequency as tf
from doc_ray.tests._mutations_common import _aconn_as
from doc_ray.tests.test_term_frequency_scan_db import ADMIN_PID, _scan, _seed_sentences


def test_mixed_candidate_batch_is_one_statement_and_uses_both_candidate_rules(
    pg, monkeypatch
) -> None:
    _seed_sentences(
        pg,
        [
            "Quxalgorithm design is verified.",
            "A standalone shared_buffers setting is verified.",
        ],
    )
    statements: list[str] = []
    original = terms_read._rows

    async def _spy(conn, sql, params):
        statements.append(sql)
        return await original(conn, sql, params)

    monkeypatch.setattr(terms_read, "_rows", _spy)

    scans, _total = _scan(
        {
            "phrase": "quxalgorithm design",
            "single": "shared_buffers",
            "missing": "quxalgorithm nowhere",
        }
    )

    assert len(statements) == 1
    assert set(scans) == {"phrase", "single", "missing"}
    assert scans["phrase"].occurrence_sentences == 1
    assert scans["single"].occurrence_sentences == 1
    assert scans["missing"].occurrence_sentences == 0
    assert " AND " in statements[0]


def test_candidate_false_positives_are_removed_and_repeated_occurrences_are_counted(pg) -> None:
    _seed_sentences(
        pg,
        [
            "The quxalgorithm designed option is only a candidate.",
            "Quxalgorithm design and quxalgorithm-design are both verified.",
        ],
    )

    scans, _total = _scan({"a": "quxalgorithm design"})

    assert scans["a"].hits == 2
    assert scans["a"].occurrence_sentences == 1
    assert scans["a"].occurrences == 2
    assert scans["a"].profile == "surface_word_v1"


def test_candidate_cap_makes_all_three_counts_floors(pg) -> None:
    _seed_sentences(
        pg,
        ["Quxbounded phrase appears here." for _index in range(3)],
    )

    scans, _total = _scan({"a": "quxbounded phrase"}, cap=2)

    assert scans["a"].hits == 2
    assert scans["a"].occurrence_sentences == 2
    assert scans["a"].occurrences == 2
    assert scans["a"].capped is True


def test_graphql_returns_verified_profile_and_counts_end_to_end(pg) -> None:
    from doc_ray.entrypoints.api.context import Loaders, RequestContext
    from doc_ray.entrypoints.api.schema import build_schema
    from doc_ray.entrypoints.api.security import Principal

    _seed_sentences(
        pg,
        [
            "The quxgraph designed result is only a candidate.",
            "Quxgraph design and quxgraph-design are verified.",
        ],
    )

    async def check():
        aconn = await _aconn_as(ADMIN_PID)
        try:
            principal = Principal(
                subject="issue-46-integration",
                principal_id=ADMIN_PID,
                scopes=frozenset({"admin"}),
            )
            ctx = RequestContext(conn=aconn, principal=principal, loaders=Loaders(aconn))
            return await build_schema().execute(
                """
                query {
                  termFrequency(terms: ["quxgraph design"]) {
                    method profile sentenceHits occurrenceSentences occurrences
                    totalSentences capped floodRisk attested note
                  }
                }
                """,
                context_value=ctx,
            )
        finally:
            await aconn.close()

    result = asyncio.run(check())
    assert result.errors is None
    row = result.data["termFrequency"][0]
    assert row["method"] == tf.METHOD_SENTENCE_SCAN
    assert row["profile"] == "surface_word_v1"
    assert row["sentenceHits"] == 2
    assert row["occurrenceSentences"] == 1
    assert row["occurrences"] == 2
    assert row["attested"] is True
