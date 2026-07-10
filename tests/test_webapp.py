"""Tests for the Flask browser (webapp).

The DB access helpers (``q`` / ``q1``) are stubbed with a dispatcher that keys
off the SQL, so these run without Postgres — they exercise the route logic and
template rendering (POS bars, KWIC highlighting, pagination, 404s, empty states).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from webapp import app as webapp

DOC_ID = "11111111-1111-1111-1111-111111111111"
MISSING_ID = "22222222-2222-2222-2222-222222222222"
LEX_UID = "lx_en.v_say01"

_DOC = {
    "id": DOC_ID,
    "title": "orwell1984",
    "doc_type": "pdf",
    "language": "en",
    "char_count": 583865,
    "page_count": 284,
    "status": "extracted",
}
_LEX = {"uid": LEX_UID, "lang": "en", "pos": "VERB", "lemma": "say"}


def _norm(sql: str) -> str:
    return " ".join(sql.split())


def _fake_health_q(s: str):
    if "FROM documents d" in s and "ORDER BY d.created_at" in s:
        return [dict(_DOC, language=None, sentences=6002, mentions=0, links=0)]
    if "FROM jobs GROUP BY kind, status" in s:
        return [
            {"kind": "annotate", "status": "failed", "count": 2},
            {"kind": "ingest", "status": "succeeded", "count": 1},
        ]
    if "FROM jobs WHERE status = 'running'" in s:
        return [
            {
                "id": "job-stale",
                "kind": "canonicalize",
                "locked_by": "worker-1",
                "heartbeat_age_s": 301,
            }
        ]
    if "FROM pg_stat_activity" in s:
        return [
            {
                "pid": 42,
                "state": "active",
                "wait_event_type": "Lock",
                "duration_s": 10,
                "query": "update documents set title = 'x'",
            }
        ]
    if "FROM pg_stat_user_tables" in s:
        return [
            {
                "relname": "documents",
                "n_live_tup": 8000,
                "n_dead_tup": 2500,
                "dead_pct": 23.8,
            }
        ]
    return None


def _fake_workflow_q(s: str):
    if "FROM seed_suggestion WHERE document_id" in s:
        return []
    if "FROM jobs WHERE document_id" in s and "kind = 'annotate'" in s:
        return [{"status": "succeeded"}]
    if "FROM jobs WHERE document_id" in s:
        return [
            {
                "id": "job-1",
                "kind": "ingest",
                "status": "succeeded",
                "error": None,
                "result": {"document_id": DOC_ID},
                "created_at": datetime(2026, 7, 4, 12, 0),
                "finished_at": datetime(2026, 7, 4, 12, 1),
            }
        ]
    if "FROM backups ORDER BY created_at DESC" in s:
        return []
    return None


def _fake_document_q(s: str):
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
    return None


def _fake_lexeme_q(s: str):
    if "FROM lexemes l" in s and "LEFT JOIN lexeme_occurrences" in s:
        return [{"uid": LEX_UID, "lang": "en", "pos": "VERB", "lemma": "say", "uses": 496}]
    if "DISTINCT lang FROM lexemes" in s:
        return [{"lang": "en"}, {"lang": "pt"}]
    if "DISTINCT pos FROM lexemes" in s:
        return [{"pos": "NOUN"}, {"pos": "VERB"}]
    if "AS form" in s:
        return [{"form": "said"}, {"form": "says"}]
    if "AS kwic" in s:
        return [
            {
                "doc_id": DOC_ID,
                "title": "orwell1984",
                "char_start": 3,
                "kwic": "He said hello there",
                "surface": "said",
            }
        ]
    return None


def _fake_search_q(s: str):
    if "FROM sentences s" in s and "ILIKE" in s:
        return [
            {
                "doc_id": DOC_ID,
                "title": "orwell1984",
                "ordinal": 3,
                "text": "Winston loved Big Brother.",
            }
        ]
    return None


def fake_q(sql, params=()):
    s = _norm(sql)
    for handler in (
        _fake_health_q,
        _fake_workflow_q,
        _fake_document_q,
        _fake_lexeme_q,
        _fake_search_q,
    ):
        rows = handler(s)
        if rows is not None:
            return rows
    return []


def fake_q1(sql, params=()):
    s = _norm(sql)
    if "FROM tokens" in s and "entity_mention" in s and "canonical" in s:
        return {
            "documents": 5,
            "sentences": 15200,
            "tokens": 100000,
            "entity_mentions": 0,
            "entity_links": 0,
            "canonical_entries": 3,
            "lexemes": 30013,
            "embeddings": 0,
        }
    if "FROM documents) docs" in s:
        return {"docs": 5, "sents": 15200, "lexemes": 30013, "occ": 283809}
    if "pg_database_size(current_database()) AS bytes" in s:
        return {"bytes": 1048576, "pretty": "1024 kB"}
    if "max_connections FROM pg_settings" in s:
        return {"max_connections": 10}
    if "FROM jobs WHERE id" in s:
        return {
            "id": params[0],
            "kind": "ingest",
            "status": "succeeded",
            "document_id": DOC_ID,
            "error": None,
            "result": {"document_id": DOC_ID},
            "created_at": datetime(2026, 7, 4, 12, 0),
            "finished_at": datetime(2026, 7, 4, 12, 1),
        }
    if "SELECT id, title FROM documents WHERE id" in s:
        return {"id": DOC_ID, "title": _DOC["title"]}
    if "SELECT * FROM documents WHERE id" in s:
        return _DOC if str(params[0]) == DOC_ID else None
    if "count(*) n FROM sentences WHERE document_id" in s:
        return {"n": 120}  # 120 / 40 per page -> 3 pages
    if "substring(full_text FROM" in s:
        return {"t": "He said hello"}  # raw slice for annotation
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
    webapp.app.config.pop("OPERATOR_HEALTH_TOKEN", None)
    return webapp.app.test_client()


# --- documents index -------------------------------------------------------


def test_home_lists_documents_and_totals(client):
    html = client.get("/").get_data(as_text=True)
    assert "orwell1984" in html
    assert f"/doc/{DOC_ID}" in html
    assert "283,809" in html  # occurrence total, comma-formatted


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


def test_home_states_product_value_proposition(client):
    html = client.get("/").get_data(as_text=True)
    normalized = _norm(html)

    assert "Ingest documents, annotate text, and curate domain entities" in normalized
    assert "auditable research corpus" in normalized


def test_home_paginates_and_shows_next_link(client, monkeypatch):
    captured = {}

    def paginated_q(sql, params=()):
        s = _norm(sql)
        if "FROM documents d" in s and "ORDER BY d.char_count" in s:
            captured["params"] = params
            return [dict(_DOC, n_sentences=6002, tools="spacy/en_core_web_sm")] * 50
        return fake_q(sql, params)

    monkeypatch.setattr(webapp, "q", paginated_q)

    html = client.get("/?page=2").get_data(as_text=True)

    assert captured["params"]["offset"] == 50
    assert captured["params"]["limit"] == 50
    assert 'href="?page=1"' in html  # prev
    assert 'href="?page=3"' in html  # a full page implies a next page


def test_home_last_page_has_no_next_link(client):
    # The default fixture returns a single row: fewer than a full page.
    html = client.get("/").get_data(as_text=True)
    assert "next →" not in html


def test_header_groups_reading_and_admin_navigation(client):
    html = client.get("/").get_data(as_text=True)
    normalized = _norm(html)

    assert 'class="nav-group nav-reading"' in html
    assert 'class="nav-group nav-admin"' in html
    assert normalized.index("Corpus") < normalized.index("Upload") < normalized.index("Backups")


# --- document page ---------------------------------------------------------


def test_document_page_shows_pos_and_verbs(client):
    html = client.get(f"/doc/{DOC_ID}").get_data(as_text=True)
    assert "Parts of speech" in html
    assert ">NOUN<" in html and ">VERB<" in html
    assert "Most-used verbs" in html and "say" in html
    assert "George Orwell" in html


def test_document_annotated_reader_highlights_words(client):
    html = client.get(f"/doc/{DOC_ID}").get_data(as_text=True)  # annotate on by default
    assert 'class="w p-VERB"' in html
    assert 'data-t="say · VERB"' in html
    assert f"/lexeme/{LEX_UID}" in html
    assert "said" in html and "hello" in html


def test_document_annotated_reader_uses_non_color_pos_cues(client):
    html = client.get(f"/doc/{DOC_ID}").get_data(as_text=True)
    normalized = _norm(html)

    assert 'data-pos="VERB"' in html
    assert 'aria-label="said, lemma say, part of speech VERB"' in html
    assert '<span class="pos-sigil" aria-hidden="true">V</span>said' in html
    assert ".w.p-VERB { border-bottom-style:double;" in normalized


def test_document_plain_mode_has_no_word_spans(client):
    html = client.get(f"/doc/{DOC_ID}?annotate=0").get_data(as_text=True)
    assert "He said hello" in html
    assert 'class="w ' not in html


def test_document_reader_renders_markdown_blocks_with_annotations(client, monkeypatch):
    full_text = "# Chapter One\n- said hello"

    def markdown_q(sql, params=()):
        s = _norm(sql)
        if "sen.ordinal, sen.text" in s:
            return [
                {"ordinal": 0, "text": "# Chapter One", "ss": 0, "se": 13},
                {"ordinal": 1, "text": "- said hello", "ss": 14, "se": 26},
            ]
        if "FROM lexeme_occurrences o" in s and "sp.char_start >=" in s:
            return [{"a": 16, "b": 20, "uid": LEX_UID, "lemma": "say", "pos": "VERB"}]
        return fake_q(sql, params)

    def markdown_q1(sql, params=()):
        if "substring(full_text FROM" in _norm(sql):
            return {"t": full_text}
        return fake_q1(sql, params)

    monkeypatch.setattr(webapp, "q", markdown_q)
    monkeypatch.setattr(webapp, "q1", markdown_q1)

    html = client.get(f"/doc/{DOC_ID}").get_data(as_text=True)

    assert '<h2 class="reader-heading h1">' in html
    assert "Chapter One" in html
    assert '<ul class="reader-list">' in html
    assert "# Chapter One" not in html
    assert "- said hello" not in html
    assert 'class="w p-VERB"' in html
    assert f"/lexeme/{LEX_UID}" in html


def test_document_unannotated_reader_explains_queued_annotation(client, monkeypatch):
    def unannotated_q(sql, params=()):
        s = _norm(sql)
        if "GROUP BY l.pos" in s:
            return []
        if "l.pos = 'VERB'" in s and "GROUP BY l.uid, l.lemma" in s:
            return []
        if "FROM lexeme_occurrences o" in s and "sp.char_start >=" in s:
            return []
        if "FROM jobs WHERE document_id" in s and "kind = 'annotate'" in s:
            return [{"status": "queued"}]
        return fake_q(sql, params)

    monkeypatch.setattr(webapp, "q", unannotated_q)

    html = client.get(f"/doc/{DOC_ID}").get_data(as_text=True)

    assert "has extracted text, but annotation has" in html
    assert "not finished yet" in html
    assert "annotation job is queued" in html
    assert "The plain text is readable now" in html


def test_document_page_shows_metadata_edit_form(client):
    html = client.get(f"/doc/{DOC_ID}").get_data(as_text=True)
    assert f'action="/doc/{DOC_ID}/metadata"' in html
    assert 'name="title" value="orwell1984"' in html
    assert 'name="tags"' in html and 'name="description"' in html


def test_document_page_shows_metadata_edit_history(client, monkeypatch):
    def with_history(sql, params=()):
        s = _norm(sql)
        if "FROM document_metadata_history" in s:
            return [
                {
                    "field": "title",
                    "old_value": "Old Title",
                    "new_value": "orwell1984",
                    "changed_at": datetime(2026, 7, 5, 12, 0),
                    "actor": "tester",
                }
            ]
        return fake_q(sql, params)

    monkeypatch.setattr(webapp, "q", with_history)

    html = client.get(f"/doc/{DOC_ID}").get_data(as_text=True)

    assert "Edit history" in html
    assert "changed" in html and "title" in html
    assert "Old Title" in html and "tester" in html


def test_document_metadata_update_calls_mutation_and_redirects(client, monkeypatch):
    captured = {}

    def fake_gql(query, variables=None):
        captured["query"] = query
        captured["variables"] = variables
        return {"updateDocumentMetadata": {"id": DOC_ID}}

    monkeypatch.setattr(webapp, "gql", fake_gql)

    resp = client.post(
        f"/doc/{DOC_ID}/metadata",
        data={"title": "New Title", "description": "A summary.", "tags": "a, b"},
    )

    assert resp.status_code == 302
    assert f"/doc/{DOC_ID}" in resp.headers["Location"]
    assert "metadata_queued=1" in resp.headers["Location"]
    assert "updateDocumentMetadata" in captured["query"]
    assert captured["variables"] == {
        "id": DOC_ID,
        "title": "New Title",
        "description": "A summary.",
        "tags": ["a", "b"],
    }


def test_document_metadata_update_error_redirects_with_message(client, monkeypatch):
    def raising_gql(query, variables=None):
        raise RuntimeError("permission denied")

    monkeypatch.setattr(webapp, "gql", raising_gql)

    resp = client.post(f"/doc/{DOC_ID}/metadata", data={"title": "New Title"})

    assert resp.status_code == 302
    assert "metadata_error=permission+denied" in resp.headers["Location"]


def test_document_pagination_math(client):
    # 120 sentences / 40 per page -> 3 pages; page 2 offers prev and next.
    html = client.get(f"/doc/{DOC_ID}?page=2").get_data(as_text=True)
    assert "2 / 3" in html
    assert "prev" in html and "next" in html


def test_document_full_scroll_view_disables_sentence_pager(client, monkeypatch):
    sentence_queries = []

    def full_q(sql, params=()):
        s = _norm(sql)
        if "sen.ordinal, sen.text" in s:
            sentence_queries.append(s)
            return [
                {"ordinal": 0, "text": "First sentence.", "ss": 0, "se": 15},
                {"ordinal": 119, "text": "Last sentence.", "ss": 16, "se": 30},
            ]
        if "FROM lexeme_occurrences o" in s and "sp.char_start >=" in s:
            return []
        return fake_q(sql, params)

    def full_q1(sql, params=()):
        if "substring(full_text FROM" in _norm(sql):
            return {"t": "First sentence. Last sentence."}
        return fake_q1(sql, params)

    monkeypatch.setattr(webapp, "q", full_q)
    monkeypatch.setattr(webapp, "q1", full_q1)

    html = client.get(f"/doc/{DOC_ID}?view=all").get_data(as_text=True)

    assert "Full document" in html
    assert "Last sentence." in html
    assert '<div class="pager">' not in html
    assert sentence_queries and " LIMIT " not in sentence_queries[0]


def test_document_reader_has_solo_pos_focus_controls(client):
    html = client.get(f"/doc/{DOC_ID}").get_data(as_text=True)

    assert 'id="pos-focus"' in html
    assert '<option value="VERB">VERB</option>' in html
    assert 'reader.classList.add("solo-" + focus)' in html
    assert ".reader.solo-VERB .w:not(.p-VERB)" in html


def test_document_missing_returns_404(client):
    assert client.get(f"/doc/{MISSING_ID}").status_code == 404


def test_annotate_html_wraps_words_preserves_gaps_and_escapes():
    from webapp.app import annotate_html

    occs = [
        {"rel_start": 0, "rel_end": 2, "uid": "lx_en.o_he", "lemma": "he", "pos": "PRON"},
        {"rel_start": 3, "rel_end": 7, "uid": LEX_UID, "lemma": "say", "pos": "VERB"},
    ]
    out = str(annotate_html("He said hello", occs))
    assert 'class="w p-VERB"' in out
    assert 'data-pos="VERB"' in out
    assert 'aria-label="said, lemma say, part of speech VERB"' in out
    assert '<span class="pos-sigil" aria-hidden="true">V</span>said' in out
    assert out.count("<a ") == 2
    assert out.endswith(" hello")  # trailing gap preserved verbatim
    assert str(annotate_html("<x>", [])) == "&lt;x&gt;"  # html-escaped


# --- lexeme index ----------------------------------------------------------


def test_lexemes_index_renders_and_filters(client):
    html = client.get("/lexemes?pos=VERB&lang=en").get_data(as_text=True)
    assert "say" in html
    assert "496" in html
    # the selected filter values are reflected in the form
    assert 'value="VERB"' in html or ">VERB<" in html


def test_lexemes_index_does_not_foreground_raw_identity(client):
    html = client.get("/lexemes?pos=VERB&lang=en").get_data(as_text=True)

    assert "<th>Identity</th>" not in html
    assert f">{LEX_UID}<" not in html
    assert f'href="/lexeme/{LEX_UID}"' in html


def test_lexemes_empty_state(client, monkeypatch):
    monkeypatch.setattr(
        webapp,
        "q",
        lambda sql, params=(): [] if "LEFT JOIN lexeme_occurrences" in sql else fake_q(sql, params),
    )
    html = client.get("/lexemes?q=zzzzz").get_data(as_text=True)
    assert "No lexemes match." in html


# --- lexeme detail (KWIC) --------------------------------------------------


def test_lexeme_kwic_highlights_the_surface(client):
    html = client.get(f"/lexeme/{LEX_UID}").get_data(as_text=True)
    assert "say" in html
    assert "said" in html  # an observed form
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


def test_search_paginates_and_shows_next_link(client, monkeypatch):
    captured = {}
    row = {"doc_id": DOC_ID, "title": "orwell1984", "ordinal": 3, "text": "Winston loved Big Brother."}

    def paginated_q(sql, params=()):
        s = _norm(sql)
        if "FROM sentences s" in s and "ILIKE" in s:
            captured["params"] = params
            return [dict(row) for _ in range(100)]
        return fake_q(sql, params)

    monkeypatch.setattr(webapp, "q", paginated_q)

    html = client.get("/search?q=Winston&page=2").get_data(as_text=True)

    assert captured["params"]["offset"] == 100
    assert captured["params"]["limit"] == 100
    assert 'href="?q=Winston&page=1"' in html
    assert 'href="?q=Winston&page=3"' in html


def test_search_last_page_has_no_next_link(client):
    # The default fixture returns a single row: fewer than a full page.
    html = client.get("/search?q=Winston").get_data(as_text=True)
    assert "next →" not in html


def test_search_discloses_literal_substring_semantics(client):
    html = client.get("/search").get_data(as_text=True)
    normalized = _norm(html)
    assert "literal substring match" in normalized
    assert "will not match inflected variants" in normalized


def test_search_empty_state_explains_literal_match_and_alternative(client, monkeypatch):
    def empty_search(sql, params=()):
        if "FROM sentences s" in _norm(sql) and "ILIKE" in _norm(sql):
            return []
        return fake_q(sql, params)

    monkeypatch.setattr(webapp, "q", empty_search)

    html = client.get("/search?q=run").get_data(as_text=True)

    assert "No sentences match." in html
    assert "literal substring search" in html
    assert "running" in html and "ran" in html
    assert "fullTextSearch" in html


def test_search_without_query_shows_no_results_table(client):
    html = client.get("/search").get_data(as_text=True)
    # The results table (and its header) render only once a term is given.
    assert "<th>Sentence</th>" not in html


# --- help / guide ----------------------------------------------------------


def test_help_entry_is_reachable_from_every_page(client):
    home = client.get("/").get_data(as_text=True)
    guide = client.get("/help").get_data(as_text=True)

    assert 'href="/help"' in home
    assert "doc-ray guide" in guide
    assert 'id="pipeline-status"' in guide
    assert 'id="domain-seeding"' in guide
    assert 'id="backups"' in guide


def test_technical_screens_link_to_contextual_help(client):
    job = client.get("/job/job-1").get_data(as_text=True)
    seeding = client.get(f"/doc/{DOC_ID}/seeding").get_data(as_text=True)
    backups = client.get("/backups").get_data(as_text=True)

    assert 'href="/help#pipeline-status"' in job
    assert 'href="/help#domain-seeding"' in seeding
    assert 'href="/help#backups"' in backups


def test_seeding_screen_signposts_workflow_and_confirms_seed(client):
    html = client.get(f"/doc/{DOC_ID}/seeding").get_data(as_text=True)
    normalized = _norm(html)

    assert (
        "Mine candidates -> audit identities -> approve exact matches -> seed approved rows"
        in normalized
    )
    assert "Seeded rows enter the shared canonical registry" in normalized
    assert "confirm(" in html
    assert "Seed approved rows permanently?" in html


def test_help_glossary_defines_core_terms(client):
    html = client.get("/help").get_data(as_text=True)
    normalized = _norm(html)

    assert 'id="glossary"' in html
    assert "Lexeme" in html
    assert "one normalized vocabulary identity" in normalized
    assert "Canonicalize" in html
    assert "links mentions to known canonical entities" in normalized
    assert "Part-of-speech codes" in html


def test_feature_pages_carry_domain_definitions(client):
    upload = _norm(client.get("/upload").get_data(as_text=True))
    lexicon = _norm(client.get("/lexemes").get_data(as_text=True))
    seeding = _norm(client.get(f"/doc/{DOC_ID}/seeding").get_data(as_text=True))

    assert "Annotate builds sentences, lexemes, and entity mentions" in upload
    assert "Canonicalize links known mentions to canonical entities" in upload
    assert "A lexeme is one normalized vocabulary identity" in lexicon
    assert "Domain seeding is the curation loop" in seeding


# --- job status ------------------------------------------------------------


def test_job_status_humanizes_success_outcome(client):
    html = client.get("/job/job-1").get_data(as_text=True)

    assert "Upload and ingest" in html
    assert "Completed" in html
    assert "The document was uploaded and registered." in html
    assert "&gt;ingest&lt;" not in html
    assert "{&#39;document_id&#39;" not in html


def test_job_status_table_does_not_foreground_raw_job_id(client):
    html = client.get("/job/job-1").get_data(as_text=True)

    assert "<th>job</th>" not in html
    assert "<th>step</th>" in html
    assert ">job-1<" not in html
    assert "Job reference" in html


def test_job_status_humanizes_failed_outcome(client, monkeypatch):
    failed_job = {
        "id": "job-failed",
        "kind": "annotate",
        "status": "failed",
        "document_id": DOC_ID,
        "error": 'ValueError("unsupported language")',
        "result": None,
        "created_at": datetime(2026, 7, 4, 12, 0),
        "finished_at": datetime(2026, 7, 4, 12, 1),
    }

    def failed_q(sql, params=()):
        if "FROM jobs WHERE document_id" in _norm(sql):
            return [failed_job]
        return fake_q(sql, params)

    def failed_q1(sql, params=()):
        if "FROM jobs WHERE id" in _norm(sql):
            return failed_job
        return fake_q1(sql, params)

    monkeypatch.setattr(webapp, "q", failed_q)
    monkeypatch.setattr(webapp, "q1", failed_q1)

    html = client.get("/job/job-failed").get_data(as_text=True)

    assert "Annotate text did not finish." in html
    assert "unsupported language." in html
    assert "Restart annotation after the worker or input issue is fixed." in html
    assert "Needs attention" in html
    assert "ValueError" not in html


def test_status_pills_expose_accessible_definitions(client, monkeypatch):
    def backup_q(sql, params=()):
        if "FROM backups ORDER BY created_at DESC" in _norm(sql):
            return [
                {
                    "id": "backup-1",
                    "status": "available",
                    "created_at": datetime(2026, 7, 4, 12, 0),
                    "table_rows": 10,
                    "blob_count": 1,
                    "doc_count": 1,
                    "archive_bytes": 1048576,
                    "note": "smoke",
                    "error": None,
                }
            ]
        return fake_q(sql, params)

    monkeypatch.setattr(webapp, "q", backup_q)

    job = client.get("/job/job-1").get_data(as_text=True)
    backups = client.get("/backups").get_data(as_text=True)

    assert 'class="tip st st-succeeded"' in job
    assert 'tabindex="0"' in job
    assert 'role="tooltip"' in job
    assert "Completed successfully" in job
    assert "Backup archive is ready to download or restore" in backups


# --- operator health -------------------------------------------------------


def test_operator_health_requires_admin_token(client):
    webapp.app.config["OPERATOR_HEALTH_TOKEN"] = "secret"

    assert client.get("/operator/health").status_code == 404
    assert client.get("/operator/health?token=wrong").status_code == 404


def test_operator_health_renders_corpus_and_database_snapshots(client):
    webapp.app.config["OPERATOR_HEALTH_TOKEN"] = "secret"

    html = client.get("/operator/health?token=secret").get_data(as_text=True)
    normalized = _norm(html)

    assert "Operator health" in html
    assert "Corpus health" in html
    assert "Database health" in html
    assert "document-unannotated" in html
    assert "job-failed" in html
    assert "job-stalled" in html
    assert "query-blocked" in html
    assert "table-bloat" in html
    assert "error group" in normalized
    assert "warning group" in normalized
    assert "info group" in normalized


def test_operator_health_json_has_stable_contract(client):
    webapp.app.config["OPERATOR_HEALTH_TOKEN"] = "secret"

    response = client.get("/operator/health.json?token=secret")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["schema_version"] == "operator-health.v1"
    assert set(payload) == {"schema_version", "corpus", "database"}
    assert set(payload["corpus"]) == {"totals", "issues", "issue_groups", "jobs"}
    assert set(payload["database"]) == {
        "size",
        "connections",
        "tables",
        "issues",
        "issue_groups",
    }
    assert payload["corpus"]["totals"]["documents"] == 5
    assert any(
        issue["code"] == "job-failed" and issue["severity"] == "error"
        for issue in payload["corpus"]["issues"]
    )
    assert any(issue["code"] == "document-unannotated" for issue in payload["corpus"]["issues"])
    assert any(issue["code"] == "query-blocked" for issue in payload["database"]["issues"])


def test_operator_health_pills_and_pos_chips_expose_accessible_definitions(client):
    webapp.app.config["OPERATOR_HEALTH_TOKEN"] = "secret"

    doc = client.get(f"/doc/{DOC_ID}").get_data(as_text=True)
    health = client.get("/operator/health?token=secret").get_data(as_text=True)

    assert "Common noun" in doc
    assert "Verb or predicate" in doc
    assert 'class="tip chip p-NOUN"' in doc
    assert 'data-pos="NOUN"' in doc
    assert 'tabindex="0"' in doc
    assert "Needs operator attention" in health
    assert 'role="tooltip"' in health


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
                    rendered = query.as_string(None)  # psycopg Composed
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
    assert "SET ROLE" in joined and "docray_client" in joined  # dropped to RLS role
    # principal is bound as a parameter (not string-interpolated)
    assert any(
        "set_config" in q and p == ("11111111-1111-1111-1111-111111111111",) for q, p in fake.calls
    )


def test_get_conn_is_superuser_passthrough_without_principal(monkeypatch):
    fake = _RecordingConn()
    monkeypatch.setattr(webapp.psycopg, "connect", lambda *a, **k: fake)
    monkeypatch.setattr(webapp, "PRINCIPAL_ID", None)
    with webapp.app.test_request_context():
        webapp.get_conn()
    assert fake.calls == []  # no SET ROLE / set_config


# ---------------------------------------------------------------------------
# Database-unavailable error page: psycopg.OperationalError must not reach
# the raw Werkzeug debugger (connection refused, bad credentials, etc).
# ---------------------------------------------------------------------------


def test_database_unavailable_renders_graceful_page_not_a_traceback(monkeypatch):
    def _raise(*_a, **_k):
        raise webapp.psycopg.OperationalError(
            'connection failed: FATAL:  password authentication failed for user "docray"'
        )

    monkeypatch.setattr(webapp.psycopg, "connect", _raise)
    monkeypatch.setattr(webapp, "PRINCIPAL_ID", None)
    # TESTING=True (the shared `client` fixture's default) makes Flask
    # propagate non-HTTP exceptions for the test runner to see directly,
    # which is right for most tests but is exactly what we're overriding
    # here: this test wants the *production* behavior of the errorhandler.
    webapp.app.config.update(TESTING=False)
    try:
        resp = webapp.app.test_client().get("/backups")
    finally:
        webapp.app.config.update(TESTING=True)
    body = resp.get_data(as_text=True)
    assert resp.status_code == 503
    assert "Can't reach the database" in body
    assert "password authentication failed" in body  # actionable detail, not swallowed
    assert "Traceback" not in body and "Werkzeug Debugger" not in body
