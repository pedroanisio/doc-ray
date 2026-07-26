"""BL-164: a search result that was NOT truncated must say so.

Split from ``test_mcp_corpus.py`` for D-015 headroom, mirroring the
``corpus.py`` -> ``shaping.py`` production split: this file covers
:func:`doc_ray.entrypoints.mcp.shaping._search_coverage`'s completeness contract,
while ``test_mcp_corpus.py`` covers token forwarding and variable mapping.

The gap this closes. ``search_sentences`` announced itself only when it *failed*
to be complete (``limit_reached`` + a flood note). Success was silent, so
``hit_count`` could not be told apart from "the first page of many", and an empty
``hits`` could not be told apart from "my query was wrong". Callers that build
grounded documents have to state absence — and silence is not a foundation for
that claim, so they hedged, or invented sharded corpus sweeps to re-prove what a
single call already knew.

``coverage.exhaustive`` is the affirmative signal: true means the scan ran to
completion within the caller's visible corpus, so ``hit_count`` IS the total.
The one trap it must not fall into is paging: a continuation page that returns
nothing means "no more after this cursor", never "absent from the corpus".
"""

from __future__ import annotations

import pytest

# Import the server eagerly so the `mcp` SDK loads (with the real httpx) before
# any test patches httpx.AsyncClient — tokens._current_request() lazily imports
# it, and the SDK evaluates `httpx.AsyncClient | None` at import time.
from doc_ray.entrypoints.mcp import corpus, server  # noqa: F401

_DOC = "11111111-1111-1111-1111-111111111111"


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
    monkeypatch.setattr(corpus.httpx, "AsyncClient", lambda **kw: client)
    return client


def _hits(count):
    return [
        {"id": str(i), "ordinal": i, "pre": "", "hit": "x", "post": "", "cursor": "c"}
        for i in range(count)
    ]


@pytest.mark.asyncio
async def test_a_zero_hit_first_page_declares_itself_an_exhaustive_scan(monkeypatch):
    _patch(monkeypatch, {"data": {"searchSentences": []}})

    out = await corpus.search_sentences("pgbouncer")

    assert out["coverage"]["exhaustive"] is True
    note = out["note"] or ""
    assert "exhaustive" in note.lower()
    assert "pgbouncer" in note  # names the string that was actually scanned for


@pytest.mark.asyncio
async def test_the_absence_note_bounds_its_own_claim_to_the_literal_string(monkeypatch):
    # The claim that is true (this exact string does not occur) must not be read as
    # the claim that is not (the corpus has nothing on the subject).
    _patch(monkeypatch, {"data": {"searchSentences": []}})

    out = await corpus.search_sentences("pgbouncer")

    note = (out["note"] or "").lower()
    assert "literal string" in note
    assert "spelling" in note or "synonym" in note


@pytest.mark.asyncio
async def test_an_unmaxed_page_of_hits_is_exhaustive_so_hit_count_is_the_total(monkeypatch):
    # The generalized case: 3 hits under a limit of 20 means there are exactly 3.
    _patch(monkeypatch, {"data": {"searchSentences": _hits(3)}})

    out = await corpus.search_sentences("kwisatz", limit=20)

    assert out["coverage"]["exhaustive"] is True
    assert out["coverage"]["hit_count"] == 3


@pytest.mark.asyncio
async def test_a_page_of_hits_stays_quiet_because_the_rows_are_the_answer(monkeypatch):
    # Completeness travels in `coverage`, not in prose. Narrating every ordinary
    # result would spend tokens telling the caller what it can already see.
    _patch(monkeypatch, {"data": {"searchSentences": _hits(3)}})

    out = await corpus.search_sentences("kwisatz", limit=20)

    assert out["note"] is None


@pytest.mark.asyncio
async def test_a_maxed_out_result_is_never_exhaustive(monkeypatch):
    _patch(monkeypatch, {"data": {"searchSentences": _hits(20)}})

    out = await corpus.search_sentences("the", limit=20)

    assert out["coverage"]["exhaustive"] is False
    assert "flood" in (out["note"] or "").lower()


@pytest.mark.asyncio
async def test_a_clamped_request_is_never_exhaustive(monkeypatch):
    # BL-162 interaction: 200 rows served against a request for 500 is truncation.
    # `exhaustive` must agree with `limit_reached` rather than contradict it.
    _patch(monkeypatch, {"data": {"searchSentences": _hits(corpus.MAX_PAGE_SIZE)}})

    out = await corpus.search_sentences("the", limit=500)

    assert out["coverage"]["limit_reached"] is True
    assert out["coverage"]["exhaustive"] is False


@pytest.mark.asyncio
async def test_a_zero_hit_continuation_page_is_not_an_absence_claim(monkeypatch):
    # The trap: paging past the end returns zero hits too. That means "no more
    # after this cursor", NOT "absent from the corpus" — the earlier pages had
    # hits. Claiming absence here would be a fresh false negative.
    _patch(monkeypatch, {"data": {"searchSentences": []}})

    out = await corpus.search_sentences("Berlin", after="cur-9")

    assert out["coverage"]["exhaustive"] is False
    note = (out["note"] or "").lower()
    assert "exhaustive" not in note
    assert "cursor" in note or "page" in note


@pytest.mark.asyncio
async def test_a_short_continuation_page_is_not_exhaustive_either(monkeypatch):
    # Same rule with rows present: the last page of a sweep is not the whole sweep.
    _patch(monkeypatch, {"data": {"searchSentences": _hits(2)}})

    out = await corpus.search_sentences("Berlin", limit=20, after="cur-9")

    assert out["coverage"]["exhaustive"] is False


@pytest.mark.asyncio
async def test_a_zero_hit_document_scoped_search_scopes_its_absence_claim(monkeypatch):
    # Absence within one document is not absence from the corpus; the note must
    # not overstate what was scanned.
    _patch(monkeypatch, {"data": {"searchSentences": []}})

    out = await corpus.search_sentences("pgbouncer", document_id=_DOC)

    assert out["coverage"]["exhaustive"] is True
    assert "document" in (out["note"] or "").lower()


@pytest.mark.asyncio
async def test_a_corpus_wide_absence_note_does_not_claim_more_than_the_visible_corpus(
    monkeypatch,
):
    # RLS scopes every read to the caller's tenant, so "the corpus" in an absence
    # note means the corpus THIS caller can see — never the whole installation.
    _patch(monkeypatch, {"data": {"searchSentences": []}})

    out = await corpus.search_sentences("pgbouncer")

    assert "visible to you" in (out["note"] or "")
