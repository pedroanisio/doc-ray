"""Term-frequency pre-flight (issue #16): verdict logic + MCP tool surface.

The database-free half of the coverage. The pure verdict logic lives in
``doc_ray.domain.term_frequency`` and is exercised here at and around every
threshold boundary — the part a fixture cannot prove without pinning corpus size.
The MCP tool half stubs ``httpx.AsyncClient`` (like test_mcp_p1.py) to prove the
tool's local behaviour: batching, the unsupported document-scoped path, and the
empty-batch rejection, all without a network. The real counts and the
single-statement / RLS properties are proven in test_term_frequency_db.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from doc_ray.adapters import persistence_read_terms as terms_read
from doc_ray.domain import term_frequency as tf
from doc_ray.domain.lexemes import iter_words, norm_lemma
from doc_ray.entrypoints.api.structure_types import TermFrequency
from doc_ray.entrypoints.mcp import corpus, structure

# --------------------------------------------------------------------------
# 0. D-024 regression — the read must never aggregate the fact table again.
# --------------------------------------------------------------------------


def test_the_query_reads_the_rollup_never_the_lexeme_occurrences_fact_table():
    # The measured 37 s Lexicon regression came from aggregating the 17.5M-row
    # lexeme_occurrences table live. This is the specific defect that must not
    # come back: the pre-flight reads the lexeme_stats rollup only.
    sql = terms_read._TERM_FREQ_SQL.lower()
    assert "lexeme_stats" in sql
    assert "lexeme_occurrences" not in sql
    assert "unnest(" in sql  # the batch is one statement over an array, not a loop


# --------------------------------------------------------------------------
# 1. flood_risk categorization — the verdict is the product (FR-2).
# --------------------------------------------------------------------------


def test_flood_risk_is_high_at_and_above_the_high_cut_point():
    total = 1_000_000
    at = round(tf.HIGH_SHARE * total)
    assert tf.flood_risk(at, total) == "high"
    assert tf.flood_risk(at * 2, total) == "high"


def test_flood_risk_is_moderate_between_the_two_cut_points():
    total = 1_000_000
    just_below_high = round(tf.HIGH_SHARE * total) - 1
    at_moderate = round(tf.MODERATE_SHARE * total)
    assert tf.flood_risk(just_below_high, total) == "moderate"
    assert tf.flood_risk(at_moderate, total) == "moderate"


def test_flood_risk_is_low_below_the_moderate_cut_point():
    total = 1_000_000
    just_below_moderate = round(tf.MODERATE_SHARE * total) - 1
    assert tf.flood_risk(just_below_moderate, total) == "low"
    assert tf.flood_risk(1, total) == "low"


def test_flood_risk_of_an_unattested_or_empty_scope_is_low_not_a_crash():
    # uses == 0 (unattested) and total == 0 (empty corpus) must both be low, and
    # crucially must not divide by zero.
    assert tf.flood_risk(0, 1_000_000) == "low"
    assert tf.flood_risk(0, 0) == "low"
    assert tf.flood_risk(5, 0) == "low"


# --------------------------------------------------------------------------
# 2. prepare_terms — batch input validation (FR-3) and FR-5 normalization.
# --------------------------------------------------------------------------


def test_prepare_terms_normalizes_with_the_lexeme_functions_norm_lemma():
    # FR-5: the key we look up must be the exact string the lexeme writer stored,
    # so the frequency describes the term the lexeme layer actually indexed.
    (prepared,) = tf.prepare_terms(["  SPICE  "])
    assert prepared.key == norm_lemma("SPICE") == "spice"
    assert prepared.term == "SPICE"  # the echoed spelling is the caller's, trimmed


def test_prepare_terms_deduplicates_by_normalized_key_preserving_first_order():
    prepared = tf.prepare_terms(["Spice", "sandworm", "SPICE", "spice"])
    assert [p.key for p in prepared] == ["spice", "sandworm"]
    assert prepared[0].term == "Spice"  # first spelling wins


def test_prepare_terms_recognizes_a_whitespace_joined_phrase_as_measurable():
    (prepared,) = tf.prepare_terms(["  Design Decision  "])

    assert prepared.key == "design decision"
    assert prepared.indexable is True
    assert prepared.parts == ("design", "decision")
    assert prepared.measurement == tf.COMPONENT_UPPER_BOUND
    assert tf.lookup_keys([prepared]) == ["design", "decision"]


def test_prepare_terms_drops_only_whitespace_blanks_never_silently_drops_a_token():
    # Whitespace-only inputs normalize to "" and are dropped — they are not terms.
    # A non-empty non-word token (an em dash) is KEPT rather than silently
    # vanishing, honouring the repo's no-silent-drop rule. BL-164: it comes back
    # *unknown*, not "unattested" — the lexeme layer never tokenizes punctuation,
    # so the index cannot represent it and has no evidence either way.
    prepared = tf.prepare_terms(["   ", "spice", "\t", "—"])
    assert [p.key for p in prepared] == ["spice", "—"]
    (_, dash) = tf.build_verdicts(prepared, {"spice": 10}, 1000)
    assert dash.indexable is False
    assert dash.attested is None and dash.flood_risk == tf.UNKNOWN


def test_prepare_terms_rejects_an_empty_or_all_blank_batch():
    with pytest.raises(tf.InvalidTerms):
        tf.prepare_terms([])
    with pytest.raises(tf.InvalidTerms):
        tf.prepare_terms(["  ", "\n"])


def test_prepare_terms_rejects_an_oversized_batch():
    with pytest.raises(tf.InvalidTerms):
        tf.prepare_terms([f"term{i}" for i in range(tf.MAX_TERMS + 1)])


# --------------------------------------------------------------------------
# 3. build_verdicts — assembly, notes (FR-4), and the unattested path (FR-6).
# --------------------------------------------------------------------------


def test_build_verdicts_carries_the_numbers_that_justify_each_verdict():
    prepared = tf.prepare_terms(["the", "sandworm"])
    total = 1_000_000
    counts = {"the": 50_000, "sandworm": 30}  # 5% and 0.003%
    common, rare = tf.build_verdicts(prepared, counts, total)
    assert (common.uses, common.total_uses, common.flood_risk) == (50_000, total, "high")
    assert common.share == pytest.approx(0.05)
    assert common.attested is True
    assert (rare.uses, rare.flood_risk, rare.attested) == (30, "low", True)


def test_a_high_risk_verdict_carries_a_steering_note_in_the_established_voice():
    # FR-4: it must not just judge — it must suggest the cheaper path.
    prepared = tf.prepare_terms(["spice"])
    (verdict,) = tf.build_verdicts(prepared, {"spice": 40_000}, 1_000_000)
    assert verdict.flood_risk == "high"
    note = verdict.note or ""
    assert "read_document_outline" in note or "read_document_sentences" in note
    assert "rarer" in note.lower() or "scene" in note.lower()


def test_an_unattested_term_is_distinguishable_and_says_so():
    # FR-6: absent from the lexicon -> uses 0, low, and an "unattested" note, so
    # the agent learns the search will likely return nothing. Absence must be
    # distinguishable from a failed lookup.
    prepared = tf.prepare_terms(["zephyrquux"])
    (verdict,) = tf.build_verdicts(prepared, {}, 1_000_000)
    assert (verdict.uses, verdict.flood_risk, verdict.attested) == (0, "low", False)
    assert "unattested" in (verdict.note or "").lower()


def test_build_verdicts_preserves_input_order_and_length():
    prepared = tf.prepare_terms(["alpha", "beta", "gamma"])
    verdicts = tf.build_verdicts(prepared, {"beta": 10}, 1000)
    assert [v.normalized_term for v in verdicts] == ["alpha", "beta", "gamma"]


def test_phrase_frequency_is_an_explicit_rarest_component_upper_bound():
    prepared = tf.prepare_terms(["design decision"])

    (verdict,) = tf.build_verdicts(prepared, {"design": 120, "decision": 3}, 100_000)

    assert verdict.measurement == tf.COMPONENT_UPPER_BOUND
    assert verdict.indexable is True
    assert verdict.uses == 3
    assert verdict.share == pytest.approx(0.00003)
    assert verdict.flood_risk == "low"
    assert verdict.attested is None
    assert [(part.part, part.uses) for part in verdict.parts] == [
        ("design", 120),
        ("decision", 3),
    ]
    note = (verdict.note or "").lower()
    assert "upper bound" in note
    assert "phrase" in note
    assert TermFrequency.from_verdict(verdict).measurement == tf.COMPONENT_UPPER_BOUND


def test_graphql_type_exposes_an_inconsistent_denominator_as_null() -> None:
    """The GraphQL contract is nullable so issue #42 can fail honestly, not as 43-of-0."""
    prepared = tf.prepare_terms(["Algorithm Design"])
    (verdict,) = tf.build_verdicts(
        prepared,
        {"algorithm": 500, "design": 300},
        10_000,
        scans={prepared[0].key: tf.SentenceScan(hits=43, capped=False)},
        total_sentences=0,
    )

    shaped = TermFrequency.from_verdict(verdict)

    assert shaped.sentence_hits == 43
    assert shaped.total_sentences is None
    assert shaped.note is not None and "unavailable" in shaped.note.lower()


def test_an_absent_phrase_component_proves_the_phrase_is_unattested():
    prepared = tf.prepare_terms(["design zephyrquux"])

    (verdict,) = tf.build_verdicts(prepared, {"design": 120, "zephyrquux": 0}, 100_000)

    assert verdict.measurement == tf.COMPONENT_UPPER_BOUND
    assert verdict.uses == 0
    assert verdict.attested is False
    assert "unattested" in (verdict.note or "").lower()


def test_non_whitespace_compounds_remain_unavailable_not_phrase_estimates():
    (verdict,) = tf.build_verdicts(
        tf.prepare_terms(["shared_buffers"]), {"shared": 12, "buffers": 70}, 1000
    )

    assert verdict.measurement == tf.UNAVAILABLE
    assert verdict.indexable is False
    assert verdict.uses is None


# --------------------------------------------------------------------------
# 4. BL-164 — terms the lexeme index structurally cannot represent.
#
# The defect: `lexemes._WORD_RE` excludes `_` and `\d` from word characters, so
# the lexeme index can never hold a token containing either. `norm_lemma` — the
# lookup key builder — does not tokenize, so the key `shared_buffers` was queried
# against an index that cannot contain it. The lookup returned 0 and the verdict
# reported `attested=false` + "a search for it will most likely return nothing"
# for a term that occurs throughout the corpus. Measured against production:
# `term_frequency("shared_buffers")` said unattested while `search_sentences`
# returned a full page of hits and tripped `limit_reached`.
#
# The rule these tests pin: the pre-flight may say "absent" ONLY for terms the
# index can actually represent. For anything else the honest answer is unknown.
# --------------------------------------------------------------------------


UNINDEXABLE = [
    "shared_buffers",  # underscore compound — the reported case
    "work_mem",
    "pg_stat_activity",
    "autovacuum_max_workers",
    "COVID-19",  # hyphen + digits: iter_words truncates to "COVID"
    "IPv6",  # trailing digit: truncates to "IPv"
    "x86_64",  # both defects at once: truncates to "x"
    "MP3",
]

INDEXABLE = [
    "spice",
    "sandworm",
    "B-tree",  # hyphen between letter runs survives the tokenizer intact
    "don't",  # internal apostrophe likewise
    "Zeitgeist",
]


@pytest.mark.parametrize("term", UNINDEXABLE)
def test_a_term_the_lexeme_index_cannot_tokenize_is_flagged_unindexable(term):
    (prepared,) = tf.prepare_terms([term])
    assert prepared.indexable is False, f"{term!r} must not be looked up as one lexeme"


@pytest.mark.parametrize("term", INDEXABLE)
def test_an_ordinary_word_is_still_indexable_and_looked_up_directly(term):
    (prepared,) = tf.prepare_terms([term])
    assert prepared.indexable is True
    assert prepared.parts == (prepared.key,)


@pytest.mark.parametrize("term", INDEXABLE)
def test_the_tokenizer_and_the_lookup_key_agree_for_every_indexable_term(term):
    # The root-cause invariant. A term is indexable only if the production
    # tokenizer yields exactly one word whose normalized form IS the lookup key —
    # i.e. only if the index can actually contain that key. This is the guard that
    # fails if either `_WORD_RE` or `norm_lemma` drifts away from the other.
    (prepared,) = tf.prepare_terms([term])
    words = [norm_lemma(w) for w, _, _ in iter_words(term)]
    assert words == [prepared.key]


@pytest.mark.parametrize("term", UNINDEXABLE)
def test_an_unindexable_term_is_never_reported_as_unattested(term):
    # The defect verbatim: a zero count against an index that cannot hold the key
    # is not evidence of absence, and must never be dressed as one.
    prepared = tf.prepare_terms([term])
    (verdict,) = tf.build_verdicts(prepared, {}, 1_000_000)
    assert verdict.attested is None, "absence cannot be claimed from an impossible lookup"
    assert verdict.uses is None, "0 would read as a measured count; the count is unknown"
    assert verdict.share is None
    assert verdict.flood_risk == tf.UNKNOWN
    note = (verdict.note or "").lower()
    assert "unattested" not in note
    assert "return nothing" not in note


def test_an_unindexable_terms_note_explains_the_gap_and_names_the_tool_that_works():
    # search_sentences matches a case-insensitive literal substring, so it handles
    # these terms correctly. The note must send the caller there instead of
    # letting them abandon a perfectly good query.
    prepared = tf.prepare_terms(["shared_buffers"])
    (verdict,) = tf.build_verdicts(prepared, {"shared": 12, "buffers": 70}, 1_000_000)
    note = verdict.note or ""
    assert "search_sentences" in note
    assert "unknown" in note.lower()


def test_an_unindexable_term_reports_the_parts_the_index_actually_holds():
    # Evidence, not just a shrug: the caller sees which lexemes the compound was
    # split into and how common each is, so they can judge for themselves.
    prepared = tf.prepare_terms(["shared_buffers"])
    assert prepared[0].parts == ("shared", "buffers")
    (verdict,) = tf.build_verdicts(prepared, {"shared": 12, "buffers": 70}, 1_000_000)
    assert [(p.part, p.uses) for p in verdict.parts] == [("shared", 12), ("buffers", 70)]


def test_lookup_keys_asks_the_adapter_for_the_parts_of_unindexable_terms():
    # The resolver reads counts for these keys in the SAME single statement — the
    # part counts must not cost an extra round-trip (the D-024 batching property).
    prepared = tf.prepare_terms(["shared_buffers", "spice"])
    keys = tf.lookup_keys(prepared)
    assert set(keys) == {"shared", "buffers", "spice"}
    assert len(keys) == len(set(keys)), "keys must be de-duplicated before the query"


def test_lookup_keys_never_asks_for_a_key_the_index_cannot_hold():
    # The wasted half of the defect: querying `shared_buffers` was guaranteed to
    # miss. Nothing unindexable may reach the SQL.
    prepared = tf.prepare_terms(UNINDEXABLE)
    for key in tf.lookup_keys(prepared):
        assert "_" not in key and not any(ch.isdigit() for ch in key)


def test_a_genuinely_unattested_word_is_still_reported_as_absent():
    # The fix must not blunt the real signal: a well-formed word the index CAN
    # hold, with no uses, is genuinely absent and must still say so.
    prepared = tf.prepare_terms(["zephyrquux"])
    (verdict,) = tf.build_verdicts(prepared, {}, 1_000_000)
    assert verdict.indexable is True
    assert verdict.attested is False and verdict.uses == 0
    assert "unattested" in (verdict.note or "").lower()


def test_an_unindexable_term_does_not_poison_the_batch():
    # Mixed batches are the normal case; the indexable terms keep exact verdicts.
    prepared = tf.prepare_terms(["shared_buffers", "the", "zephyrquux"])
    compound, common, absent = tf.build_verdicts(
        prepared, {"shared": 12, "buffers": 70, "the": 50_000}, 1_000_000
    )
    assert compound.flood_risk == tf.UNKNOWN
    assert (common.flood_risk, common.attested, common.uses) == ("high", True, 50_000)
    assert (absent.flood_risk, absent.attested, absent.uses) == ("low", False, 0)


# --------------------------------------------------------------------------
# 5. MCP tool — batching, unsupported document scope, empty-batch rejection.
# --------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json, headers):
        self.calls.append((url, json, headers))
        return _FakeResp(self.payload)


@pytest.fixture(autouse=True)
def _reset_token():
    corpus.caller_token.set("")
    yield


def _patch(monkeypatch, payload):
    client = _FakeClient(payload)
    monkeypatch.setattr(corpus, "acquire_client", AsyncMock(return_value=client))
    return client


def _row(
    term,
    uses,
    total,
    risk,
    attested,
    note=None,
    indexable=True,
    parts=(),
    measurement="exact",
):
    return {
        "term": term,
        "normalizedTerm": term.lower(),
        "uses": uses,
        "totalUses": total,
        "share": (uses / total) if (total and uses is not None) else 0.0,
        "floodRisk": risk,
        "attested": attested,
        "indexable": indexable,
        "measurement": measurement,
        "parts": [{"part": p, "uses": u} for p, u in parts],
        "note": note,
    }


@pytest.mark.asyncio
async def test_term_frequency_selects_the_indexability_fields_over_graphql(monkeypatch):
    # BL-164: the honesty signal is worthless if the MCP tool does not ask for it.
    # (That the selection is *valid* against the schema is proven in
    # test_mcp_schema_contract.py; this pins that it is actually requested.)
    client = _patch(monkeypatch, {"data": {"termFrequency": []}})
    await structure.term_frequency(["shared_buffers"])
    query = client.calls[0][1]["query"]
    for field in ("measurement", "indexable", "parts", "part"):
        assert field in query, f"term_frequency must select {field}"


@pytest.mark.asyncio
async def test_term_frequency_triages_a_batch_in_one_call(monkeypatch):
    payload = {
        "data": {
            "termFrequency": [
                _row("spice", 40_000, 1_000_000, "high", True, "…flood…"),
                _row("zephyrquux", 0, 1_000_000, "low", False, "…unattested…"),
            ]
        }
    }
    client = _patch(monkeypatch, payload)
    out = await structure.term_frequency(["spice", "zephyrquux"])
    assert [r["floodRisk"] for r in out] == ["high", "low"]
    assert len(client.calls) == 1  # FR-3: one round-trip for the whole batch
    assert client.calls[0][1]["variables"]["t"] == ["spice", "zephyrquux"]


@pytest.mark.asyncio
async def test_mcp_preserves_the_graphql_missing_denominator_contract(monkeypatch):
    """MCP must carry GraphQL null + the actionable note without reshaping either."""
    row = _row("Algorithm Design", 300, 10_000, "low", True)
    row.update(
        {
            "method": "sentence_scan",
            "sentenceHits": 43,
            "totalSentences": None,
            "capped": False,
            "note": "totalSentences unavailable — denominator rollup empty",
        }
    )
    _patch(monkeypatch, {"data": {"termFrequency": [row]}})

    (result,) = await structure.term_frequency(["Algorithm Design"])

    assert result["sentenceHits"] == 43
    assert result["totalSentences"] is None
    assert "unavailable" in result["note"].lower()


@pytest.mark.asyncio
async def test_term_frequency_rejects_document_scope_typed_and_loud(monkeypatch):
    # Open decision 1(a): corpus-wide only for now; a document_id must fail typed
    # and loud (unsupported), never silently return corpus-wide numbers mislabelled
    # as document-scoped — and it must not spend a round-trip doing so.
    client = _patch(monkeypatch, {"data": {"termFrequency": []}})
    with pytest.raises(corpus.ToolCallError) as ei:
        await structure.term_frequency(["spice"], document_id=_DOC)
    assert ei.value.kind == "unsupported"
    assert client.calls == []


@pytest.mark.asyncio
async def test_term_frequency_rejects_a_malformed_document_id_before_the_network(monkeypatch):
    client = _patch(monkeypatch, {"data": {"termFrequency": []}})
    with pytest.raises(corpus.ToolCallError) as ei:
        await structure.term_frequency(["spice"], document_id="not-a-uuid")
    assert ei.value.kind == "bad_input"
    assert client.calls == []


@pytest.mark.asyncio
async def test_term_frequency_rejects_an_empty_batch_before_the_network(monkeypatch):
    client = _patch(monkeypatch, {"data": {"termFrequency": []}})
    with pytest.raises(corpus.ToolCallError) as ei:
        await structure.term_frequency([])
    assert ei.value.kind == "bad_input"
    assert client.calls == []


_DOC = "11111111-1111-1111-1111-111111111111"
