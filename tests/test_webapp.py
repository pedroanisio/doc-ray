"""Tests for the Flask browser (webapp).

The DB access helpers (``q`` / ``q1``) are stubbed with a dispatcher that keys
off the SQL, so these run without Postgres — they exercise the route logic and
template rendering (POS bars, KWIC highlighting, pagination, 404s, empty states).
"""

from __future__ import annotations

import pytest

from webapp import app as webapp

DOC_ID = "11111111-1111-1111-1111-111111111111"
MISSING_ID = "22222222-2222-2222-2222-222222222222"
LEX_UID = "lx_en.v_say01"

_DOC = {
    "id": DOC_ID, "title": "orwell1984", "doc_type": "pdf", "language": "en",
    "char_count": 583865, "page_count": 284, "status": "extracted",
}
_LEX = {"uid": LEX_UID, "lang": "en", "pos": "VERB", "lemma": "say"}


def _norm(sql: str) -> str:
    return " ".join(sql.split())


def fake_q(sql, params=()):
    s = _norm(sql)
    if "FROM documents d" in s and "ORDER BY d.char_count" in s:
        return [dict(_DOC, n_sentences=6002, tools="spacy/en_core_web_sm")]
    if "document_authors" in s:
        return [{"name": "George Orwell"}]
    if "document_tags" in s:
        return []
    if "GROUP BY l.pos" in s:
        return [{"pos": "NOUN", "n": 100}, {"pos": "VERB", "n": 40}]
    if "l.pos = 'VERB'" in s and "GROUP BY l.uid, l.lemma" in s:
        return [{"uid": LEX_UID, "lemma": "say", "n": 40}]
    if "sen.ordinal, sen.text" in s:
        return [{"ordinal": 0, "text": "He said hello", "ss": 0, "se": 13}]
    if "FROM lexeme_occurrences o" in s and "sp.char_start >=" in s:
        return [
            {"a": 0, "b": 2, "uid": "lx_en.o_he", "lemma": "he", "pos": "PRON"},
            {"a": 3, "b": 7, "uid": LEX_UID, "lemma": "say", "pos": "VERB"},
            {"a": 8, "b": 13, "uid": "lx_en.x_hi", "lemma": "hello", "pos": "INTJ"},
        ]
    if "FROM lexemes l" in s and "LEFT JOIN lexeme_occurrences" in s:
        return [{"uid": LEX_UID, "lang": "en", "pos": "VERB", "lemma": "say", "uses": 496}]
    if "DISTINCT lang FROM lexemes" in s:
        return [{"lang": "en"}, {"lang": "pt"}]
    if "DISTINCT pos FROM lexemes" in s:
        return [{"pos": "NOUN"}, {"pos": "VERB"}]
    if "AS form" in s:
        return [{"form": "said"}, {"form": "says"}]
    if "AS kwic" in s:
        return [{"doc_id": DOC_ID, "title": "orwell1984", "char_start": 3,
                 "kwic": "He said hello there", "surface": "said"}]
    if "FROM sentences s" in s and "ILIKE" in s:
        return [{"doc_id": DOC_ID, "title": "orwell1984", "ordinal": 3,
                 "text": "Winston loved Big Brother."}]
    return []


def fake_q1(sql, params=()):
    s = _norm(sql)
    if "FROM documents) docs" in s:
        return {"docs": 5, "sents": 15200, "lexemes": 30013, "occ": 283809}
    if "SELECT * FROM documents WHERE id" in s:
        return _DOC if str(params[0]) == DOC_ID else None
    if "count(*) n FROM sentences WHERE document_id" in s:
        return {"n": 120}                       # 120 / 40 per page -> 3 pages
    if "substring(full_text FROM" in s:
        return {"t": "He said hello"}            # raw slice for annotation
    if "SELECT * FROM lexemes WHERE uid" in s:
        return _LEX if params[0] == LEX_UID else None
    if "count(*) n FROM lexeme_occurrences WHERE lexeme_uid" in s:
        return {"n": 496}
    return None


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(webapp, "q", fake_q)
    monkeypatch.setattr(webapp, "q1", fake_q1)
    webapp.app.config.update(TESTING=True)
    return webapp.app.test_client()


# --- documents index -------------------------------------------------------

def test_home_lists_documents_and_totals(client):
    html = client.get("/").get_data(as_text=True)
    assert "orwell1984" in html
    assert f"/doc/{DOC_ID}" in html
    assert "283,809" in html          # occurrence total, comma-formatted


def test_home_lede_uses_live_document_count(client, monkeypatch):
    def totals_41(sql, params=()):
        if "FROM documents) docs" in _norm(sql):
            return {"docs": 41, "sents": 15200, "lexemes": 30013, "occ": 283809}
        return fake_q1(sql, params)

    monkeypatch.setattr(webapp, "q1", totals_41)

    html = client.get("/").get_data(as_text=True)

    assert "41 documents, decomposed" in _norm(html)
    assert "Five documents" not in html


def test_home_empty_corpus_shows_upload_call_to_action(client, monkeypatch):
    def empty_docs(sql, params=()):
        if "FROM documents d" in _norm(sql) and "ORDER BY d.char_count" in _norm(sql):
            return []
        return fake_q(sql, params)

    def empty_totals(sql, params=()):
        if "FROM documents) docs" in _norm(sql):
            return {"docs": 0, "sents": 0, "lexemes": 0, "occ": 0}
        return fake_q1(sql, params)

    monkeypatch.setattr(webapp, "q", empty_docs)
    monkeypatch.setattr(webapp, "q1", empty_totals)

    html = client.get("/").get_data(as_text=True)

    assert "No documents yet." in html
    assert "Upload the first document" in html
    assert 'href="/upload"' in html


# --- document page ---------------------------------------------------------

def test_document_page_shows_pos_and_verbs(client):
    html = client.get(f"/doc/{DOC_ID}").get_data(as_text=True)
    assert "Parts of speech" in html
    assert ">NOUN<" in html and ">VERB<" in html
    assert "Most-used verbs" in html and "say" in html
    assert "George Orwell" in html


def test_document_annotated_reader_highlights_words(client):
    html = client.get(f"/doc/{DOC_ID}").get_data(as_text=True)   # annotate on by default
    assert 'class="w p-VERB"' in html
    assert 'data-t="say · VERB"' in html
    assert f"/lexeme/{LEX_UID}" in html
    assert "said" in html and "hello" in html


def test_document_plain_mode_has_no_word_spans(client):
    html = client.get(f"/doc/{DOC_ID}?annotate=0").get_data(as_text=True)
    assert "He said hello" in html
    assert 'class="w ' not in html


def test_document_pagination_math(client):
    # 120 sentences / 40 per page -> 3 pages; page 2 offers prev and next.
    html = client.get(f"/doc/{DOC_ID}?page=2").get_data(as_text=True)
    assert "2 / 3" in html
    assert "prev" in html and "next" in html


def test_document_missing_returns_404(client):
    assert client.get(f"/doc/{MISSING_ID}").status_code == 404


def test_annotate_html_wraps_words_preserves_gaps_and_escapes():
    from webapp.app import annotate_html
    occs = [
        {"rel_start": 0, "rel_end": 2, "uid": "lx_en.o_he", "lemma": "he", "pos": "PRON"},
        {"rel_start": 3, "rel_end": 7, "uid": LEX_UID, "lemma": "say", "pos": "VERB"},
    ]
    out = str(annotate_html("He said hello", occs))
    assert '<a class="w p-VERB" href="/lexeme/lx_en.v_say01" data-t="say · VERB">said</a>' in out
    assert out.count("<a ") == 2
    assert out.endswith(" hello")                 # trailing gap preserved verbatim
    assert str(annotate_html("<x>", [])) == "&lt;x&gt;"   # html-escaped


# --- lexeme index ----------------------------------------------------------

def test_lexemes_index_renders_and_filters(client):
    html = client.get("/lexemes?pos=VERB&lang=en").get_data(as_text=True)
    assert "say" in html and LEX_UID in html
    assert "496" in html
    # the selected filter values are reflected in the form
    assert 'value="VERB"' in html or ">VERB<" in html


def test_lexemes_empty_state(client, monkeypatch):
    monkeypatch.setattr(webapp, "q",
                        lambda sql, params=(): [] if "LEFT JOIN lexeme_occurrences" in sql
                        else fake_q(sql, params))
    html = client.get("/lexemes?q=zzzzz").get_data(as_text=True)
    assert "No lexemes match." in html


# --- lexeme detail (KWIC) --------------------------------------------------

def test_lexeme_kwic_highlights_the_surface(client):
    html = client.get(f"/lexeme/{LEX_UID}").get_data(as_text=True)
    assert "say" in html
    assert "said" in html                       # an observed form
    # KWIC: 'He said hello there' split around 'said', surface highlighted
    assert ">said</span>" in html
    assert "He " in html and "hello there" in html


def test_lexeme_missing_returns_404(client):
    assert client.get("/lexeme/lx_en.v_nope").status_code == 404


# --- search ----------------------------------------------------------------

def test_search_highlights_the_term(client):
    html = client.get("/search?q=Winston").get_data(as_text=True)
    assert "<mark>Winston</mark>" in html
    assert "Big Brother" in html


def test_search_without_query_shows_no_results_table(client):
    html = client.get("/search").get_data(as_text=True)
    # The results table (and its header) render only once a term is given.
    assert "<th>Sentence</th>" not in html


# ---------------------------------------------------------------------------
# RLS enforcement: get_conn drops to docray_client + sets the principal
# ---------------------------------------------------------------------------

class _RecordingConn:
    def __init__(self):
        self.calls = []

    def cursor(self, *a, **k):
        outer = self

        class _Cur:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def execute(self_, query, params=None):
                try:
                    rendered = query.as_string(None)     # psycopg Composed
                except AttributeError:
                    rendered = str(query)
                outer.calls.append((rendered, params))

        return _Cur()

    def close(self):
        pass


def test_get_conn_enforces_rls_when_principal_configured(monkeypatch):
    fake = _RecordingConn()
    monkeypatch.setattr(webapp.psycopg, "connect", lambda *a, **k: fake)
    monkeypatch.setattr(webapp, "PRINCIPAL_ID", "11111111-1111-1111-1111-111111111111")
    monkeypatch.setattr(webapp, "DB_ROLE", "docray_client")
    with webapp.app.test_request_context():
        webapp.get_conn()
    joined = " ".join(q for q, _ in fake.calls)
    assert "SET ROLE" in joined and "docray_client" in joined   # dropped to RLS role
    # principal is bound as a parameter (not string-interpolated)
    assert any("set_config" in q and p == ("11111111-1111-1111-1111-111111111111",)
               for q, p in fake.calls)


def test_get_conn_is_superuser_passthrough_without_principal(monkeypatch):
    fake = _RecordingConn()
    monkeypatch.setattr(webapp.psycopg, "connect", lambda *a, **k: fake)
    monkeypatch.setattr(webapp, "PRINCIPAL_ID", None)
    with webapp.app.test_request_context():
        webapp.get_conn()
    assert fake.calls == []                                     # no SET ROLE / set_config
