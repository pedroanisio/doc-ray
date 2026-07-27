"""Pure and MCP contracts for verified phrase-frequency counts (#46)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from doc_ray.domain import term_frequency as tf
from doc_ray.entrypoints.api.schema import build_schema
from doc_ray.entrypoints.api.structure_types import TermFrequency
from doc_ray.entrypoints.mcp import corpus, server, structure

_TOTAL_SENTENCES = 1_500_000
_TOTAL_USES = 32_000_000


def _verdict(term: str, scan: tf.SentenceScan):
    prepared = tf.prepare_terms([term])
    return tf.build_verdicts(
        prepared,
        {},
        _TOTAL_USES,
        scans={prepared[0].key: scan},
        total_sentences=_TOTAL_SENTENCES,
    )[0]


def test_candidate_texts_are_verified_into_sentence_and_occurrence_counts() -> None:
    """A substring candidate is not a boundary-valid phrase occurrence."""
    scan = tf.verify_sentence_candidates(
        "algorithm design",
        ["The algorithm designed it.", "Algorithm Design is deliberate."],
        cap=tf.SENTENCE_HIGH_HITS,
    )

    assert scan.hits == 2
    assert scan.occurrence_sentences == 1
    assert scan.occurrences == 1
    assert scan.profile == "surface_word_v1"
    assert scan.capped is False


def test_verified_scan_counts_overlaps_and_every_occurrence_per_sentence() -> None:
    scan = tf.verify_sentence_candidates(
        "a a",
        ["a a a", "A-a"],
        cap=tf.SENTENCE_HIGH_HITS,
    )

    assert scan.hits == 2
    assert scan.occurrence_sentences == 2
    assert scan.occurrences == 3


def test_verified_count_invariants_hold_over_generated_candidate_sets() -> None:
    fixtures = (
        "algorithm designed",
        "Algorithm Design",
        "algorithm-design and algorithm  design",
        "design algorithm",
        "Tangram",
    )
    for size in range(len(fixtures) + 1):
        scan = tf.verify_sentence_candidates(
            "algorithm design", fixtures[:size], cap=tf.SENTENCE_HIGH_HITS
        )
        assert scan.occurrences >= scan.occurrence_sentences
        assert scan.occurrence_sentences <= scan.hits


@pytest.mark.parametrize(
    "scan",
    [
        {"hits": 1, "occurrence_sentences": 2, "occurrences": 2},
        {"hits": 2, "occurrence_sentences": 2, "occurrences": 1},
    ],
)
def test_verified_scan_rejects_impossible_count_relationships(scan: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        tf.SentenceScan(capped=False, profile="surface_word_v1", **scan)


def test_flood_risk_bands_on_verified_sentences_not_candidate_false_positives() -> None:
    verdict = _verdict(
        "algorithm design",
        tf.SentenceScan(
            hits=250,
            capped=False,
            occurrence_sentences=1,
            occurrences=1,
            profile="surface_word_v1",
        ),
    )

    assert verdict.flood_risk == tf.LOW
    assert verdict.sentence_hits == 250
    assert verdict.occurrence_sentences == 1
    assert verdict.occurrences == 1
    assert verdict.profile == "surface_word_v1"


def test_a_capped_candidate_scan_makes_all_verified_counts_visible_floors() -> None:
    scan = tf.verify_sentence_candidates(
        "shared_buffers",
        ["myshared_buffersx", "shared_buffers"],
        cap=2,
    )
    verdict = _verdict("shared_buffers", scan)

    assert scan.hits == 2
    assert scan.occurrence_sentences == 1
    assert scan.occurrences == 1
    assert scan.capped is True
    assert verdict.flood_risk == tf.HIGH
    assert verdict.attested is True
    assert "floor" in (verdict.note or "").lower()
    assert "surface_word_v1" in (verdict.note or "")


def test_a_capped_zero_does_not_claim_absence_from_an_incomplete_scan() -> None:
    scan = tf.verify_sentence_candidates(
        "shared_buffers",
        ["myshared_buffersx"],
        cap=1,
    )
    verdict = _verdict("shared_buffers", scan)

    assert verdict.capped is True
    assert verdict.occurrence_sentences == 0
    assert verdict.attested is None
    assert verdict.flood_risk == tf.HIGH


def test_boundary_false_positive_candidates_do_not_claim_attestation() -> None:
    scan = tf.verify_sentence_candidates(
        "shared_buffers",
        ["myshared_buffersx"],
        cap=tf.SENTENCE_HIGH_HITS,
    )
    verdict = _verdict("shared_buffers", scan)

    assert scan.hits == 1
    assert scan.occurrence_sentences == 0
    assert scan.occurrences == 0
    assert verdict.attested is False
    assert "surface_word_v1" in (verdict.note or "")


def test_false_positive_candidate_still_exposes_a_missing_denominator() -> None:
    scan = tf.verify_sentence_candidates(
        "shared_buffers",
        ["myshared_buffersx"],
        cap=tf.SENTENCE_HIGH_HITS,
    )
    prepared = tf.prepare_terms(["shared_buffers"])
    verdict = tf.build_verdicts(
        prepared,
        {},
        _TOTAL_USES,
        scans={prepared[0].key: scan},
        total_sentences=0,
    )[0]

    assert verdict.attested is False
    assert verdict.total_sentences is None
    assert "denominator" in (verdict.note or "").lower()
    assert "unavailable" in (verdict.note or "").lower()


def test_graphql_type_and_schema_expose_verified_phrase_counts() -> None:
    scan = tf.verify_sentence_candidates(
        "Algorithm Design",
        ["Algorithm Design and algorithm-design."],
        cap=tf.SENTENCE_HIGH_HITS,
    )
    shaped = TermFrequency.from_verdict(_verdict("Algorithm Design", scan))
    sdl = build_schema().as_str()
    type_sdl = sdl[sdl.index("type TermFrequency {") :]

    assert shaped.profile == "surface_word_v1"
    assert shaped.occurrence_sentences == 1
    assert shaped.occurrences == 2
    assert "profile: String" in type_sdl
    assert "occurrenceSentences: Int" in type_sdl
    assert "occurrences: Int" in type_sdl


def test_exact_single_lexeme_keeps_every_scan_field_null() -> None:
    prepared = tf.prepare_terms(["spice"])
    verdict = tf.build_verdicts(prepared, {"spice": 42}, 1_000)[0]
    shaped = TermFrequency.from_verdict(verdict)

    assert verdict.method == tf.METHOD_LEXEME
    assert (
        shaped.profile,
        shaped.sentence_hits,
        shaped.occurrence_sentences,
        shaped.occurrences,
        shaped.total_sentences,
        shaped.capped,
    ) == (None, None, None, None, None, None)


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class _Client:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def post(self, url, json, headers):
        self.calls.append((url, json, headers))
        return _Response(self.payload)


def _patch(monkeypatch, payload: dict) -> _Client:
    client = _Client(payload)
    monkeypatch.setattr(corpus, "acquire_client", AsyncMock(return_value=client))
    return client


@pytest.mark.asyncio
async def test_mcp_selects_and_preserves_verified_phrase_counts(monkeypatch) -> None:
    row = {
        "term": "Algorithm Design",
        "normalizedTerm": "algorithm design",
        "uses": 300,
        "totalUses": 10_000,
        "share": 0.03,
        "floodRisk": "low",
        "attested": True,
        "indexable": True,
        "measurement": "component_upper_bound",
        "parts": [],
        "note": None,
        "method": "sentence_scan",
        "profile": "surface_word_v1",
        "sentenceHits": 43,
        "occurrenceSentences": 31,
        "occurrences": 47,
        "totalSentences": 1_500_000,
        "capped": False,
    }
    client = _patch(monkeypatch, {"data": {"termFrequency": [row]}})

    (result,) = await structure.term_frequency(["Algorithm Design"])

    assert result["profile"] == "surface_word_v1"
    assert result["occurrenceSentences"] == 31
    assert result["occurrences"] == 47
    query = client.calls[0][1]["query"]
    for field in ("profile", "occurrenceSentences", "occurrences"):
        assert field in query, f"term_frequency must select {field}"


def test_mcp_instructions_teach_candidate_vs_verified_count_semantics() -> None:
    instructions = server.SERVER_INSTRUCTIONS

    for field in ("sentenceHits", "occurrenceSentences", "occurrences"):
        assert field in instructions
    assert "surface_word_v1" in instructions
    assert "all three counts floors" in instructions
