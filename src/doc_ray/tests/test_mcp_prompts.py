"""MCP prompts primitive (Item 8 / #13): the four user-selectable workflows.

The database-free half. Prompts go through the read tool functions
(`corpus.get_document`, `structure.read_document_outline`, …), so these tests
monkeypatch those functions directly to assert the *composition* — what the
returned message says, that it is parameterized by real data, that it is honest
about a uniform outline, that a malformed id is rejected before any call, and that
an unreadable document degrades gracefully rather than fabricating a plan. The
real-data / differ-across-documents guarantees are re-proven against a live DB in
test_mcp_prompts_db.py.
"""

from __future__ import annotations

import logging

import pytest

from doc_ray.entrypoints.mcp import audit, corpus, prompts, structure

_DOC = "11111111-1111-1111-1111-111111111111"

_HEADINGS_OUTLINE = {
    "source": "headings",
    "totalSentences": 320,
    "entryCount": 2,
    "note": "Structure from 2 detected headings.",
    "entries": [
        {"level": 1, "title": "Book One", "ordinalStart": 0, "ordinalEnd": 159, "pageStart": 1},
        {"level": 1, "title": "Book Two", "ordinalStart": 160, "ordinalEnd": 319, "pageStart": 60},
    ],
}

_UNIFORM_OUTLINE = {
    "source": "uniform",
    "totalSentences": 210,
    "entryCount": 2,
    "note": "No headings were detected — an even split.",
    "entries": [
        {"level": 1, "title": "Paul walked the sand", "ordinalStart": 0, "ordinalEnd": 104},
        {"level": 1, "title": "The spice flowed", "ordinalStart": 105, "ordinalEnd": 209},
    ],
}


def _doc(title="Dune", sentence_count=320):
    return {"id": _DOC, "title": title, "sentenceCount": sentence_count, "status": "annotated"}


@pytest.fixture
def stub(monkeypatch):
    """Monkeypatch the tool functions prompts call, recording invocations.

    Returns a dict you can set ``document``/``outline`` on and read ``calls`` from.
    """
    state = {"document": _doc(), "outline": _HEADINGS_OUTLINE, "calls": []}

    async def _get_document(document_id):
        state["calls"].append(("get_document", document_id))
        return state["document"]

    async def _read_outline(document_id, max_entries=50):
        state["calls"].append(("read_document_outline", document_id))
        if isinstance(state["outline"], Exception):
            raise state["outline"]
        return state["outline"]

    monkeypatch.setattr(corpus, "get_document", _get_document)
    monkeypatch.setattr(structure, "read_document_outline", _read_outline)
    return state


# --------------------------------------------------------------------------
# 1. summarize_document — eager, parameterized by real data (FR-2).
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarize_document_interpolates_the_real_title_count_and_ranges(stub):
    msg = await prompts.summarize_document(_DOC)
    assert "Dune" in msg  # real title
    assert "320" in msg  # real sentence count
    assert "Book One" in msg and "Book Two" in msg  # outline entry titles
    assert "0" in msg and "159" in msg and "319" in msg  # ordinal ranges to read
    # the methodology it must carry:
    assert "read_document_sentences" in msg
    assert 'response_format="concise"' in msg or "response_format='concise'" in msg
    assert "ordinal" in msg.lower()  # every claim carries a citation


@pytest.mark.asyncio
async def test_summarize_document_is_honest_when_the_outline_is_a_uniform_grid(stub):
    # FR-3: a uniform grid must not be laundered into a table of contents.
    stub["document"] = _doc(title="Dune Messiah", sentence_count=210)
    stub["outline"] = _UNIFORM_OUTLINE
    msg = await prompts.summarize_document(_DOC)
    assert "210" in msg
    low = msg.lower()
    assert "not chapter" in low or "not a table of contents" in low
    assert "uniform" in low or "even split" in low


@pytest.mark.asyncio
async def test_summarize_document_differs_for_two_documents(stub):
    # FR-2 regression: identical boilerplate for two documents would defeat the primitive.
    first = await prompts.summarize_document(_DOC)
    stub["document"] = _doc(title="Children of Dune", sentence_count=90)
    stub["outline"] = _UNIFORM_OUTLINE
    second = await prompts.summarize_document(_DOC)
    assert first != second


@pytest.mark.asyncio
async def test_summarize_document_rejects_a_malformed_id_before_any_network_call(stub):
    # FR-5: reuse _require_uuid; reject before the first tool call.
    with pytest.raises(corpus.ToolCallError) as ei:
        await prompts.summarize_document("not-a-uuid")
    assert ei.value.kind == "bad_input"
    assert stub["calls"] == []  # nothing was called


@pytest.mark.asyncio
async def test_summarize_document_of_an_unreadable_document_does_not_fabricate_a_plan(stub):
    # FR-4: a well-formed but unreadable id -> plain message, no fabricated plan,
    # and the outline is never fetched.
    stub["document"] = None
    msg = await prompts.summarize_document(_DOC)
    assert "list_documents" in msg
    assert "Book One" not in msg  # no fabricated ranges
    assert ("read_document_outline", _DOC) not in stub["calls"]


@pytest.mark.asyncio
async def test_summarize_document_degrades_when_the_outline_is_unavailable(stub):
    # Open decision 2: if the outline call fails, degrade to a self-serve plan and
    # SAY it degraded — never invent ranges.
    stub["outline"] = corpus.ToolCallError("upstream_error", "boom")
    msg = await prompts.summarize_document(_DOC)
    assert "read_document_outline" in msg  # tells the model to call it itself
    assert "Book One" not in msg  # no invented ranges
    low = msg.lower()
    assert "could not" in low or "degrad" in low or "unavailable" in low


# --------------------------------------------------------------------------
# 2. ground_a_claim — search-then-verify, parameterized by the claim.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ground_a_claim_carries_the_claim_and_the_search_then_verify_path():
    msg = await prompts.ground_a_claim("Paul becomes emperor")
    assert "Paul becomes emperor" in msg
    assert "search_sentences" in msg
    assert "read_document_sentences" in msg
    low = msg.lower()
    assert "unsupported" in low  # the non-negotiable rule
    assert "verbatim" in low or "quote" in low


@pytest.mark.asyncio
async def test_ground_a_claim_validates_an_optional_document_id(stub):
    with pytest.raises(corpus.ToolCallError) as ei:
        await prompts.ground_a_claim("some claim", document_id="nope")
    assert ei.value.kind == "bad_input"


# --------------------------------------------------------------------------
# 3. survey_entities — profiles over frequency, disagreement visible.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_survey_entities_prefers_profiles_and_explains_the_contested_signal():
    msg = await prompts.survey_entities()
    assert "entity_profiles" in msg
    assert "entity_frequency" in msg  # names what NOT to use
    low = msg.lower()
    assert "typeconsistency" in low
    assert "resolved" in low  # registry-backing caveat
    assert "search_sentences" in msg  # corroborate the contested


# --------------------------------------------------------------------------
# 4. sweep_the_corpus — map-reduce with the coverage proof.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_the_corpus_interpolates_shard_count_and_states_the_coverage_proof():
    msg = await prompts.sweep_the_corpus(shard_count=5)
    assert "corpus_manifest" in msg and "corpus_shard" in msg
    assert "5" in msg  # the chosen shard count
    low = msg.lower()
    assert "totaldocuments" in low  # the coverage proof: Σ per-shard == totalDocuments


@pytest.mark.asyncio
async def test_sweep_defaults_to_eight_shards():
    msg = await prompts.sweep_the_corpus()
    assert "8" in msg


@pytest.mark.asyncio
async def test_sweep_rejects_a_nonsense_shard_count():
    with pytest.raises(corpus.ToolCallError) as ei:
        await prompts.sweep_the_corpus(shard_count=0)
    assert ei.value.kind == "bad_input"


# --------------------------------------------------------------------------
# 5. Registry + token budget (NFR).
# --------------------------------------------------------------------------


def test_prompts_registry_is_exactly_the_four_workflows():
    names = {fn.__name__ for fn in prompts.PROMPTS}
    assert names == {"summarize_document", "ground_a_claim", "survey_entities", "sweep_the_corpus"}


@pytest.mark.asyncio
async def test_every_prompt_message_stays_within_the_token_budget(stub):
    # NFR: a prompt message is context paid for on every turn it is used. Keep each
    # comfortably bounded (prose ~1500 + interpolated data).
    messages = [
        await prompts.summarize_document(_DOC),
        await prompts.ground_a_claim("a representative claim of moderate length"),
        await prompts.survey_entities(document_id=_DOC),
        await prompts.sweep_the_corpus(shard_count=8),
    ]
    for msg in messages:
        assert 0 < len(msg) <= 2000, f"prompt message too large: {len(msg)} chars"


@pytest.mark.asyncio
async def test_an_audited_prompt_emits_exactly_one_audit_line(caplog):
    # AC: each prompt invocation emits exactly one audit line (prompts are caller
    # actions; the audit trail must not have a hole). The same wrapper server.py uses.
    wrapped = audit.audited(prompts.ground_a_claim)
    with caplog.at_level(logging.INFO, logger="doc_ray.mcp.audit"):
        await wrapped(claim="a claim")
    lines = [r for r in caplog.records if r.name == "doc_ray.mcp.audit"]
    assert len(lines) == 1
    assert "tool=ground_a_claim" in lines[0].getMessage()
    assert "outcome=ok" in lines[0].getMessage()


@pytest.mark.asyncio
async def test_prompts_never_embed_a_caller_credential(stub):
    # Security NFR: the returned text must not carry the caller's token.
    corpus.caller_token.set("super-secret-bearer-token")
    try:
        msg = await prompts.summarize_document(_DOC)
        assert "super-secret-bearer-token" not in msg
    finally:
        corpus.caller_token.set("")
