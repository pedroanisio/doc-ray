"""Tests for the Flask browser (webapp).

The DB access helpers (``q`` / ``q1``) are stubbed with a dispatcher that keys
off the SQL, so these run without Postgres — they exercise the route logic and
template rendering (POS bars, KWIC highlighting, pagination, 404s, empty states).
"""

from __future__ import annotations

import re
from datetime import datetime

import pytest
from flask import session

from webapp import app as webapp

DOC_ID = "11111111-1111-1111-1111-111111111111"
MISSING_ID = "22222222-2222-2222-2222-222222222222"
COLLECTION_ID = "55555555-5555-5555-5555-555555555555"
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
    if "FROM jobs j LEFT JOIN documents d" in s:
        return [
            {
                "id": "job-stale",
                "kind": "annotate",
                "status": "running",
                "attempts": 3,
                "max_attempts": 3,
                "locked_by": "worker-7",
                "document_id": DOC_ID,
                "document_title": "orwell1984",
                "progress_current": 3,
                "progress_total": 7,
                "progress_stage": "Registering token and entity spans",
                "runtime_s": 4389,
                "heartbeat_age_s": 14,
                "queued_age_s": 4389,
                "reclaim_after_s": 286,
                "error": "reclaimed: stale heartbeat",
            },
            {
                "id": "job-queued",
                "kind": "ingest",
                "status": "queued",
                "attempts": 0,
                "max_attempts": 3,
                "locked_by": None,
                "document_id": None,
                "document_title": None,
                "progress_current": None,
                "progress_total": None,
                "progress_stage": None,
                "runtime_s": None,
                "heartbeat_age_s": None,
                "queued_age_s": 52,
                "reclaim_after_s": None,
                "error": "",
            },
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
    if "FROM document_collections" in s:
        return []
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
    if "FROM lexemes l" in s and "JOIN lexeme_occurrences" in s and "count(o.*) AS uses" in s:
        return [{"uid": LEX_UID, "lang": "en", "pos": "VERB", "lemma": "say", "uses": 496}]
    if "SELECT DISTINCT l.lang" in s and "JOIN lexeme_occurrences" in s:
        return [{"lang": "en"}, {"lang": "pt"}]
    if "SELECT DISTINCT l.pos" in s and "JOIN lexeme_occurrences" in s:
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


def test_home_renders_pdf_document_preview_thumbnail(client):
    html = client.get("/").get_data(as_text=True)

    assert 'class="doc-preview doc-preview-image"' in html
    assert f'src="/doc/{DOC_ID}/page/1.png"' in html
    assert 'alt="First page preview of orwell1984"' in html


def test_home_renders_file_type_preview_fallback_and_status(client, monkeypatch):
    pending_id = "33333333-3333-3333-3333-333333333333"
    failed_id = "44444444-4444-4444-4444-444444444444"

    def preview_rows(sql, params=()):
        if "FROM documents d" in _norm(sql) and "ORDER BY d.char_count" in _norm(sql):
            return [
                dict(
                    _DOC,
                    id=pending_id,
                    title="meeting-notes",
                    doc_type="docx",
                    status="pending",
                    n_sentences=0,
                    tools=None,
                ),
                dict(
                    _DOC,
                    id=failed_id,
                    title="broken-import",
                    doc_type="html",
                    status="failed",
                    n_sentences=0,
                    tools=None,
                ),
            ]
        return fake_q(sql, params)

    monkeypatch.setattr(webapp, "q", preview_rows)

    html = client.get("/").get_data(as_text=True)

    assert "DOCX" in html
    assert "HTML" in html
    assert "Preview pending" in html
    assert "Preview failed" in html
    assert f"/doc/{pending_id}/page/1.png" not in html
    assert f"/doc/{failed_id}/page/1.png" not in html


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


def test_home_supports_bulk_document_selection(client):
    html = client.get("/").get_data(as_text=True)

    assert 'action="/documents/bulk"' in html
    assert f'name="document_id" value="{DOC_ID}"' in html
    assert 'name="bulk_action" value="tag"' in html
    assert 'name="bulk_action" value="archive"' in html
    assert 'name="bulk_action" value="reannotate"' in html
    assert 'name="bulk_action" value="reextract"' in html
    assert 'name="tags"' in html


def test_bulk_document_tag_posts_graphql_mutation(client, monkeypatch):
    captured = {}

    def fake_gql(query, variables=None):
        captured["query"] = query
        captured["variables"] = variables
        return {"bulkTagDocuments": [{"id": "job-1"}]}

    monkeypatch.setattr(webapp, "gql", fake_gql)

    token = _csrf_token_from(client.get("/").get_data(as_text=True))
    resp = client.post(
        "/documents/bulk",
        data={
            "document_id": [DOC_ID],
            "bulk_action": "tag",
            "tags": "reviewed, ops",
            "csrf_token": token,
        },
    )

    assert resp.status_code == 302
    assert "bulk_queued=1" in resp.headers["Location"]
    assert "bulkTagDocuments" in captured["query"]
    assert captured["variables"] == {"ids": [DOC_ID], "tags": ["reviewed", "ops"]}


def test_bulk_document_archive_posts_graphql_mutation(client, monkeypatch):
    captured = {}

    def fake_gql(query, variables=None):
        captured["query"] = query
        captured["variables"] = variables
        return {"bulkArchiveDocuments": [{"id": "job-1"}]}

    monkeypatch.setattr(webapp, "gql", fake_gql)

    token = _csrf_token_from(client.get("/").get_data(as_text=True))
    resp = client.post(
        "/documents/bulk",
        data={"document_id": [DOC_ID], "bulk_action": "archive", "csrf_token": token},
    )

    assert resp.status_code == 302
    assert "bulk_queued=1" in resp.headers["Location"]
    assert "bulkArchiveDocuments" in captured["query"]
    assert captured["variables"] == {"ids": [DOC_ID]}


def test_bulk_document_reannotate_posts_per_document_mutations(client, monkeypatch):
    calls = []

    def fake_gql(query, variables=None):
        calls.append((query, variables))
        return {"reannotate": {"id": f"job-{len(calls)}"}}

    monkeypatch.setattr(webapp, "gql", fake_gql)

    token = _csrf_token_from(client.get("/").get_data(as_text=True))
    resp = client.post(
        "/documents/bulk",
        data={
            "document_id": [DOC_ID, MISSING_ID],
            "bulk_action": "reannotate",
            "csrf_token": token,
        },
    )

    assert resp.status_code == 302
    assert "bulk_queued=2" in resp.headers["Location"]
    assert all("reannotate" in query for query, _variables in calls)
    assert [variables for _query, variables in calls] == [{"id": DOC_ID}, {"id": MISSING_ID}]


def test_bulk_document_reextract_skips_non_pdf_and_reports_skip_count(client, monkeypatch):
    calls = []

    def docs_by_id_q(sql, params=()):
        if "FROM documents WHERE id = ANY" in _norm(sql):
            return [
                {"id": DOC_ID, "doc_type": "pdf"},
                {"id": MISSING_ID, "doc_type": "html"},
            ]
        return fake_q(sql, params)

    def fake_gql(query, variables=None):
        calls.append((query, variables))
        return {"reextractDocument": {"id": "job-1"}}

    monkeypatch.setattr(webapp, "q", docs_by_id_q)
    monkeypatch.setattr(webapp, "gql", fake_gql)

    token = _csrf_token_from(client.get("/").get_data(as_text=True))
    resp = client.post(
        "/documents/bulk",
        data={
            "document_id": [DOC_ID, MISSING_ID],
            "bulk_action": "reextract",
            "csrf_token": token,
        },
    )

    assert resp.status_code == 302
    assert "bulk_queued=1" in resp.headers["Location"]
    assert "bulk_skipped=1" in resp.headers["Location"]
    assert len(calls) == 1
    assert "reextractDocument" in calls[0][0]
    assert calls[0][1] == {"id": DOC_ID}


def test_bulk_document_reprocess_rejects_batches_over_api_limit(client, monkeypatch):
    from doc_ray.entrypoints.api.context import MAX_BATCH_IDS

    calls = []
    monkeypatch.setattr(webapp, "gql", lambda *args, **kwargs: calls.append((args, kwargs)))

    token = _csrf_token_from(client.get("/").get_data(as_text=True))
    ids = [DOC_ID] * (MAX_BATCH_IDS + 1)
    resp = client.post(
        "/documents/bulk",
        data={"document_id": ids, "bulk_action": "reannotate", "csrf_token": token},
    )

    assert resp.status_code == 302
    assert f"bulk_error=batch+size+exceeds+{MAX_BATCH_IDS}" in resp.headers["Location"]
    assert calls == []


def test_header_groups_reading_and_admin_navigation(client):
    html = client.get("/").get_data(as_text=True)
    normalized = _norm(html)

    assert 'class="nav-group nav-reading"' in html
    assert 'class="nav-group nav-admin"' in html
    assert normalized.index("Corpus") < normalized.index("Upload") < normalized.index("Backups")


# --- document collections --------------------------------------------------


def test_collections_index_lists_manual_and_smart_collections(client, monkeypatch):
    def collection_q(sql, params=()):
        if "FROM document_collections" in _norm(sql):
            return [
                {
                    "id": COLLECTION_ID,
                    "name": "Cold War primary sources",
                    "description": "Manual reading set",
                    "kind": "manual",
                    "rule": {},
                    "document_count": 2,
                },
                {
                    "id": "66666666-6666-6666-6666-666666666666",
                    "name": "PDF queue",
                    "description": "Auto-filtered documents",
                    "kind": "smart",
                    "rule": {"doc_type": "pdf", "status": "extracted"},
                    "document_count": 5,
                },
            ]
        return fake_q(sql, params)

    monkeypatch.setattr(webapp, "q", collection_q)

    html = client.get("/collections").get_data(as_text=True)

    assert "Collections" in html
    assert "Cold War primary sources" in html
    assert "PDF queue" in html
    assert "manual" in html and "smart" in html
    assert f"/collections/{COLLECTION_ID}" in html
    assert 'name="kind"' in html and 'name="doc_type"' in html and 'name="tags"' in html


def test_create_manual_collection_inserts_and_redirects(client, monkeypatch):
    captured = {}

    def insert_collection(sql, params=()):
        captured["sql"] = _norm(sql)
        captured["params"] = params
        return {"id": COLLECTION_ID}

    monkeypatch.setattr(webapp, "q1", insert_collection)

    token = _csrf_token_from(client.get("/collections").get_data(as_text=True))
    resp = client.post(
        "/collections",
        data={
            "name": "Cold War primary sources",
            "description": "Manual reading set",
            "kind": "manual",
            "csrf_token": token,
        },
    )

    assert resp.status_code == 302
    assert f"/collections/{COLLECTION_ID}" in resp.headers["Location"]
    assert "INSERT INTO document_collections" in captured["sql"]
    assert captured["params"]["kind"] == "manual"
    assert captured["params"]["rule"] == "{}"


def test_collection_detail_renders_manual_members_and_remove_controls(client, monkeypatch):
    def collection_q1(sql, params=()):
        if "FROM document_collections WHERE id" in _norm(sql):
            return {
                "id": COLLECTION_ID,
                "name": "Cold War primary sources",
                "description": "Manual reading set",
                "kind": "manual",
                "rule": {},
            }
        return fake_q1(sql, params)

    def collection_q(sql, params=()):
        s = _norm(sql)
        if "FROM document_collection_members" in s:
            return [dict(_DOC, collection_id=COLLECTION_ID, collection_kind="manual")]
        if "FROM documents d" in s and "NOT EXISTS" in s:
            return []
        return fake_q(sql, params)

    monkeypatch.setattr(webapp, "q1", collection_q1)
    monkeypatch.setattr(webapp, "q", collection_q)

    html = client.get(f"/collections/{COLLECTION_ID}").get_data(as_text=True)

    assert "Cold War primary sources" in html
    assert "Manual reading set" in html
    assert "orwell1984" in html
    assert f'action="/collections/{COLLECTION_ID}/documents/{DOC_ID}"' in html
    assert 'name="action" value="remove"' in html


def test_smart_collection_detail_uses_rule_to_render_matching_documents(client, monkeypatch):
    captured = {}

    def collection_q1(sql, params=()):
        if "FROM document_collections WHERE id" in _norm(sql):
            return {
                "id": COLLECTION_ID,
                "name": "Extracted PDFs",
                "description": "Smart queue",
                "kind": "smart",
                "rule": {"doc_type": "pdf", "status": "extracted", "tags": ["primary"]},
            }
        return fake_q1(sql, params)

    def collection_q(sql, params=()):
        s = _norm(sql)
        if "FROM documents d" in s and "document_tags" in s:
            captured["params"] = params
            return [dict(_DOC, n_sentences=6002, tools="spacy/en_core_web_sm")]
        return fake_q(sql, params)

    monkeypatch.setattr(webapp, "q1", collection_q1)
    monkeypatch.setattr(webapp, "q", collection_q)

    html = client.get(f"/collections/{COLLECTION_ID}").get_data(as_text=True)

    assert "Extracted PDFs" in html
    assert "Smart rule" in html
    assert "orwell1984" in html
    assert captured["params"]["doc_type"] == "pdf"
    assert captured["params"]["status"] == "extracted"
    assert captured["params"]["tags"] == ["primary"]


def test_document_page_can_add_document_to_manual_collection(client, monkeypatch):
    def collection_q(sql, params=()):
        s = _norm(sql)
        if "FROM document_collections c" in s and "NOT EXISTS" in s:
            return [{"id": COLLECTION_ID, "name": "Cold War primary sources"}]
        return fake_q(sql, params)

    monkeypatch.setattr(webapp, "q", collection_q)

    html = client.get(f"/doc/{DOC_ID}").get_data(as_text=True)

    assert "Add to collection" in html
    assert f'action="/collections/{COLLECTION_ID}/documents/{DOC_ID}"' in html
    assert 'name="action" value="add"' in html


# --- document page ---------------------------------------------------------


def test_document_page_shows_pos_and_verbs(client):
    html = client.get(f"/doc/{DOC_ID}").get_data(as_text=True)
    assert "Parts of speech" in html
    assert ">NOUN<" in html and ">VERB<" in html
    assert "Most-used verbs" in html and "say" in html
    assert "George Orwell" in html


def test_document_page_renders_pdf_visual_preview(client):
    html = client.get(f"/doc/{DOC_ID}").get_data(as_text=True)

    assert 'class="doc-preview doc-preview-image doc-preview-large"' in html
    assert f'src="/doc/{DOC_ID}/page/1.png"' in html
    assert "First-page preview" in html


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


def test_document_page_shows_pipeline_reprocess_controls(client):
    html = client.get(f"/doc/{DOC_ID}").get_data(as_text=True)

    assert "Reprocess pipeline" in html
    assert f'action="/doc/{DOC_ID}/reprocess"' in html
    assert 'name="step" value="reextract"' in html
    assert 'name="step" value="annotate"' in html
    assert 'name="step" value="reannotate"' in html
    assert 'name="step" value="semantic_enrich"' in html
    assert 'name="step" value="canonicalize"' in html


def test_document_reprocess_posts_graphql_mutation(client, monkeypatch):
    captured = {}

    def fake_gql(query, variables=None):
        captured["query"] = query
        captured["variables"] = variables
        return {"semanticEnrichDocument": {"id": "job-1"}}

    monkeypatch.setattr(webapp, "gql", fake_gql)

    token = _csrf_token_from(client.get(f"/doc/{DOC_ID}").get_data(as_text=True))
    resp = client.post(
        f"/doc/{DOC_ID}/reprocess",
        data={"step": "semantic_enrich", "csrf_token": token},
    )

    assert resp.status_code == 302
    assert "reprocess_queued=semantic_enrich" in resp.headers["Location"]
    assert "semanticEnrichDocument" in captured["query"]
    assert captured["variables"] == {"id": DOC_ID}


def test_document_reprocess_annotate_step_posts_annotate_document_mutation(client, monkeypatch):
    """The full-annotate step must fire annotateDocument, not the lighter
    reannotate mutation — reannotate never touches tokens/entity_mention, so
    the two must never be interchangeable."""
    captured = {}

    def fake_gql(query, variables=None):
        captured["query"] = query
        captured["variables"] = variables
        return {"annotateDocument": {"id": "job-2"}}

    monkeypatch.setattr(webapp, "gql", fake_gql)

    token = _csrf_token_from(client.get(f"/doc/{DOC_ID}").get_data(as_text=True))
    resp = client.post(
        f"/doc/{DOC_ID}/reprocess",
        data={"step": "annotate", "csrf_token": token},
    )

    assert resp.status_code == 302
    assert "reprocess_queued=annotate" in resp.headers["Location"]
    assert "annotateDocument" in captured["query"]
    assert "reannotate(" not in captured["query"]


def test_document_reprocess_word_frequency_step_posts_reannotate_mutation(client, monkeypatch):
    captured = {}

    def fake_gql(query, variables=None):
        captured["query"] = query
        return {"reannotate": {"id": "job-3"}}

    monkeypatch.setattr(webapp, "gql", fake_gql)

    token = _csrf_token_from(client.get(f"/doc/{DOC_ID}").get_data(as_text=True))
    client.post(f"/doc/{DOC_ID}/reprocess", data={"step": "reannotate", "csrf_token": token})

    assert "reannotate(" in captured["query"]
    assert "annotateDocument" not in captured["query"]


def test_document_reprocess_failed_annotate_does_not_mask_by_reannotate_success(
    client, monkeypatch
):
    """Regression: annotate and reannotate used to share one UI card, so a
    reannotate success (a different, lighter operation) hid a real annotate
    failure. They are now separate steps and must stay independently tracked."""

    def mixed_kinds_q(sql, params=()):
        s = _norm(sql)
        if "FROM jobs WHERE document_id" in s and "AND status IN" not in s:
            return [
                {
                    "id": "job-reannotate-ok",
                    "kind": "reannotate",
                    "status": "succeeded",
                    "attempts": 1,
                    "max_attempts": 3,
                    "error": None,
                },
                {
                    "id": "job-annotate-failed",
                    "kind": "annotate",
                    "status": "failed",
                    "attempts": 3,
                    "max_attempts": 3,
                    "error": "reclaimed: stale heartbeat, attempts exhausted",
                },
            ]
        return fake_q(sql, params)

    monkeypatch.setattr(webapp, "q", mixed_kinds_q)

    html = client.get(f"/doc/{DOC_ID}").get_data(as_text=True)

    assert "Annotate text did not finish." in html
    assert "Retry Re-annotate" in html
    assert 'name="step" value="annotate"' in html


def test_document_reprocess_controls_show_active_job_instead_of_duplicate_button(
    client, monkeypatch
):
    def active_jobs_q(sql, params=()):
        s = _norm(sql)
        if "FROM jobs WHERE document_id" in s:
            return [{"id": "job-annotate", "kind": "reannotate", "status": "running"}]
        return fake_q(sql, params)

    monkeypatch.setattr(webapp, "q", active_jobs_q)

    html = client.get(f"/doc/{DOC_ID}").get_data(as_text=True)

    assert "Refresh word frequency is running" in html
    assert 'name="step" value="reannotate"' not in html
    assert 'name="step" value="annotate"' in html
    assert 'name="step" value="semantic_enrich"' in html


def test_document_reprocess_controls_show_failed_job_error(client, monkeypatch):
    def failed_job_q(sql, params=()):
        s = _norm(sql)
        if "FROM jobs WHERE document_id" in s and "AND status IN" not in s:
            return [
                {
                    "id": "job-annotate-3",
                    "kind": "reannotate",
                    "status": "failed",
                    "attempts": 3,
                    "max_attempts": 3,
                    "error": "reclaimed: stale heartbeat, attempts exhausted",
                }
            ]
        return fake_q(sql, params)

    monkeypatch.setattr(webapp, "q", failed_job_q)

    html = client.get(f"/doc/{DOC_ID}").get_data(as_text=True)

    assert "Refresh word frequency did not finish." in html
    assert "3/3 attempts used" in html
    assert "stale heartbeat" in html
    assert "Retry Refresh word frequency" in html
    # the step must still be actionable, not silently blocked like an active job.
    assert 'name="step" value="reannotate"' in html


def test_document_reprocess_controls_hide_superseded_failure(client, monkeypatch):
    """A later succeeded attempt must clear the earlier failure banner."""

    def superseded_q(sql, params=()):
        s = _norm(sql)
        if "FROM jobs WHERE document_id" in s and "AND status IN" not in s:
            return [
                {
                    "id": "job-annotate-2",
                    "kind": "reannotate",
                    "status": "succeeded",
                    "attempts": 1,
                    "max_attempts": 3,
                    "error": None,
                },
                {
                    "id": "job-annotate-1",
                    "kind": "reannotate",
                    "status": "failed",
                    "attempts": 3,
                    "max_attempts": 3,
                    "error": "reclaimed: stale heartbeat, attempts exhausted",
                },
            ]
        return fake_q(sql, params)

    monkeypatch.setattr(webapp, "q", superseded_q)

    html = client.get(f"/doc/{DOC_ID}").get_data(as_text=True)

    assert "did not finish" not in html
    assert 'name="step" value="reannotate"' in html


def test_document_overlay_missing_geometry_embeds_reextract_action(client, monkeypatch):
    def missing_geometry_q(sql, params=()):
        s = _norm(sql)
        if "DISTINCT sp.page_number" in s:
            return []
        return fake_q(sql, params)

    monkeypatch.setattr(webapp, "q", missing_geometry_q)

    html = client.get(f"/doc/{DOC_ID}?view=original").get_data(as_text=True)

    assert "Original overlay is not available until this PDF is re-extracted" in html
    assert 'name="step" value="reextract"' in html
    assert f'action="/doc/{DOC_ID}/reprocess"' in html


def test_document_reprocess_error_redirects_with_message(client, monkeypatch):
    def raising_gql(query, variables=None):
        raise RuntimeError("permission denied")

    monkeypatch.setattr(webapp, "gql", raising_gql)

    token = _csrf_token_from(client.get(f"/doc/{DOC_ID}").get_data(as_text=True))
    resp = client.post(
        f"/doc/{DOC_ID}/reprocess",
        data={"step": "canonicalize", "csrf_token": token},
    )

    assert resp.status_code == 302
    assert "reprocess_error=permission+denied" in resp.headers["Location"]


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


def test_document_page_shows_metadata_suggestions_and_queue_action(client, monkeypatch):
    def with_suggestions(sql, params=()):
        s = _norm(sql)
        if "FROM document_metadata_suggestion" in s:
            return [
                {
                    "id": "01KXA6SUGGESTION000000000",
                    "field": "tags",
                    "suggested_value": "graph",
                    "confidence": 0.62,
                    "reason": "Frequent noun in this document",
                    "status": "pending",
                    "source": "verified-deterministic",
                }
            ]
        return fake_q(sql, params)

    monkeypatch.setattr(webapp, "q", with_suggestions)

    html = client.get(f"/doc/{DOC_ID}").get_data(as_text=True)

    assert "Metadata suggestions" in html
    assert "graph" in html
    assert "Low confidence" in html
    assert f'action="/doc/{DOC_ID}/metadata-suggestions"' in html


def test_document_metadata_suggestion_request_calls_mutation(client, monkeypatch):
    captured = {}

    def fake_gql(query, variables=None):
        captured["query"] = query
        captured["variables"] = variables
        return {"suggestDocumentMetadata": {"id": "job-1"}}

    monkeypatch.setattr(webapp, "gql", fake_gql)
    token = _csrf_token_from(client.get(f"/doc/{DOC_ID}").get_data(as_text=True))

    resp = client.post(
        f"/doc/{DOC_ID}/metadata-suggestions",
        data={"csrf_token": token},
    )

    assert resp.status_code == 302
    assert "suggestions_queued=1" in resp.headers["Location"]
    assert "suggestDocumentMetadata" in captured["query"]
    assert captured["variables"] == {"id": DOC_ID}


def test_document_metadata_update_calls_mutation_and_redirects(client, monkeypatch):
    captured = {}

    def fake_gql(query, variables=None):
        captured["query"] = query
        captured["variables"] = variables
        return {"updateDocumentMetadata": {"id": DOC_ID}}

    monkeypatch.setattr(webapp, "gql", fake_gql)

    token = _csrf_token_from(client.get(f"/doc/{DOC_ID}").get_data(as_text=True))
    resp = client.post(
        f"/doc/{DOC_ID}/metadata",
        data={
            "title": "New Title",
            "description": "A summary.",
            "tags": "a, b",
            "csrf_token": token,
        },
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

    token = _csrf_token_from(client.get(f"/doc/{DOC_ID}").get_data(as_text=True))
    resp = client.post(f"/doc/{DOC_ID}/metadata", data={"title": "New Title", "csrf_token": token})

    assert resp.status_code == 302
    assert "metadata_error=permission+denied" in resp.headers["Location"]


def test_document_pagination_math(client):
    # 120 sentences / 40 per page -> 3 pages; page 2 offers prev and next.
    html = client.get(f"/doc/{DOC_ID}?page=2&annotate=0").get_data(as_text=True)
    assert "2 / 3" in html
    assert "prev" in html and "next" in html


def test_document_pages_tab_uses_persisted_pdf_page_boundaries(client, monkeypatch):
    sentence_queries = []

    def page_q(sql, params=()):
        s = _norm(sql)
        if "DISTINCT sp.page_number" in s:
            return [{"page_number": 1}, {"page_number": 2}]
        if "sen.ordinal, sen.text" in s:
            sentence_queries.append((s, params))
            if "sp.page_number = %s" in s:
                assert params == (DOC_ID, 2)
                return [{"ordinal": 4, "text": "Only page two.", "ss": 21, "se": 35}]
            return [{"ordinal": 0, "text": "Chunk fallback.", "ss": 0, "se": 15}]
        if "FROM lexeme_occurrences o" in s and "sp.char_start >=" in s:
            return []
        return fake_q(sql, params)

    def page_q1(sql, params=()):
        if "substring(full_text FROM" in _norm(sql):
            return {"t": "Only page two." if params[0] == 22 else "Chunk fallback."}
        return fake_q1(sql, params)

    monkeypatch.setattr(webapp, "q", page_q)
    monkeypatch.setattr(webapp, "q1", page_q1)

    html = client.get(f"/doc/{DOC_ID}?page=2&annotate=0").get_data(as_text=True)

    assert "Only page two." in html
    assert "Chunk fallback." not in html
    assert "2 / 284" in html
    assert any("sp.page_number = %s" in sql for sql, _ in sentence_queries)
    assert not any("LIMIT %s OFFSET %s" in sql for sql, _ in sentence_queries)


def test_document_original_overlay_renders_pdf_page_and_pos_boxes(client, monkeypatch):
    def overlay_q(sql, params=()):
        s = _norm(sql)
        if "DISTINCT sp.page_number" in s:
            return [{"page_number": 1}, {"page_number": 2}]
        if "sen.ordinal, sen.text" in s and "sp.page_number = %s" in s:
            return [{"ordinal": 4, "text": "Only page two.", "ss": 21, "se": 35}]
        if "document_layout_runs" in s and "page_width" in s:
            return [{"page_width": 200.0, "page_height": 100.0}]
        if "sp.bbox_x0" in s and "lexeme_occurrences" in s:
            return [
                {
                    "uid": LEX_UID,
                    "lemma": "say",
                    "pos": "VERB",
                    "bbox_x0": 20.0,
                    "bbox_y0": 10.0,
                    "bbox_x1": 80.0,
                    "bbox_y1": 30.0,
                }
            ]
        if "FROM lexeme_occurrences o" in s and "sp.char_start >=" in s:
            return []
        return fake_q(sql, params)

    monkeypatch.setattr(webapp, "q", overlay_q)

    html = client.get(f"/doc/{DOC_ID}?view=original&page=2").get_data(as_text=True)

    assert 'class="original-overlay"' in html
    assert f'src="/doc/{DOC_ID}/page/2.png"' in html
    assert 'href="/lexeme/lx_en.v_say01"' in html
    assert 'style="left:10.000%;top:10.000%;width:30.000%;height:20.000%"' in html
    assert "say · VERB" in html


def test_document_original_overlay_renders_text_free_pdf_page(client, monkeypatch):
    def empty_page_q(sql, params=()):
        s = _norm(sql)
        if "DISTINCT sp.page_number" in s:
            return [{"page_number": 2}]
        if "FROM document_pages" in s:
            assert params == (DOC_ID, 1)
            return [
                {
                    "page_width": 200.0,
                    "page_height": 100.0,
                    "has_text": False,
                    "text_run_count": 0,
                }
            ]
        if "sen.ordinal, sen.text" in s and "sp.page_number = %s" in s:
            assert params == (DOC_ID, 1)
            return []
        if "document_layout_runs" in s and "page_width" in s:
            raise AssertionError("document_pages should answer page dimensions first")
        if "FROM lexeme_occurrences o" in s and "sp.char_start >=" in s:
            return []
        return fake_q(sql, params)

    monkeypatch.setattr(webapp, "q", empty_page_q)

    html = client.get(f"/doc/{DOC_ID}?view=original&page=1").get_data(as_text=True)

    assert 'class="original-overlay"' in html
    assert f'src="/doc/{DOC_ID}/page/1.png"' in html
    assert "No text detected on this page" in html
    assert "Original overlay is not available until this PDF is re-extracted" not in html


def test_document_original_overlay_explains_missing_pdf_geometry(client, monkeypatch):
    def missing_q(sql, params=()):
        s = _norm(sql)
        if "DISTINCT sp.page_number" in s:
            return []
        return fake_q(sql, params)

    monkeypatch.setattr(webapp, "q", missing_q)

    html = client.get(f"/doc/{DOC_ID}?view=original").get_data(as_text=True)

    assert "Original overlay is not available until this PDF is re-extracted" in html
    assert 'class="original-overlay"' not in html
    assert "Rendered original PDF page" not in html


def test_document_original_overlay_explains_non_pdf_format(client, monkeypatch):
    def non_pdf_q1(sql, params=()):
        if "SELECT * FROM documents WHERE id" in _norm(sql):
            return dict(_DOC, doc_type="docx", page_count=None)
        return fake_q1(sql, params)

    monkeypatch.setattr(webapp, "q1", non_pdf_q1)

    html = client.get(f"/doc/{DOC_ID}?view=original").get_data(as_text=True)

    assert "Original markup is not available until this document is re-extracted" in html
    assert 'class="original-overlay"' not in html
    assert "Rendered original PDF page" not in html


def test_document_original_overlay_can_toggle_to_entity_canonical_boxes(client, monkeypatch):
    def entity_q(sql, params=()):
        s = _norm(sql)
        if "DISTINCT sp.page_number" in s:
            return [{"page_number": 1}, {"page_number": 2}]
        if "sen.ordinal, sen.text" in s and "sp.page_number = %s" in s:
            return [{"ordinal": 4, "text": "Only page two.", "ss": 21, "se": 35}]
        if "document_layout_runs" in s and "page_width" in s:
            return [{"page_width": 200.0, "page_height": 100.0}]
        if "FROM entity_mention em" in s and "LEFT JOIN canonical c" in s:
            return [
                {
                    "mention_id": "m1",
                    "surface": "Big Brother",
                    "entity_type": "ORG",
                    "scheme": "canonical",
                    "kb_id": "7",
                    "preferred": "Big Brother",
                    "bbox_x0": 20.0,
                    "bbox_y0": 10.0,
                    "bbox_x1": 80.0,
                    "bbox_y1": 30.0,
                }
            ]
        if "FROM lexeme_occurrences o" in s and "sp.char_start >=" in s:
            return []
        return fake_q(sql, params)

    monkeypatch.setattr(webapp, "q", entity_q)

    html = client.get(f"/doc/{DOC_ID}?view=original&page=2&overlay=entities").get_data(as_text=True)

    assert 'href="?page=2&view=original&overlay=grammar"' in html
    assert 'href="?page=2&view=original&overlay=entities"' in html
    assert 'class="overlay-box entity-box e-ORG"' in html
    assert 'href="#entity-m1"' in html
    assert "Big Brother · ORG · canonical:7" in html


def test_document_original_view_renders_markup_highlights_for_html(client, monkeypatch):
    def html_q1(sql, params=()):
        s = _norm(sql)
        if "SELECT * FROM documents WHERE id" in s:
            return dict(_DOC, doc_type="html", page_count=None)
        if "FROM document_metadata" in s and "render_html" in s:
            return {"value": "<h1>Heading</h1><p>Hello World</p>"}
        if s.startswith("SELECT full_text FROM document_content"):
            return {"full_text": "Heading Hello World"}
        return fake_q1(sql, params)

    def html_q(sql, params=()):
        s = _norm(sql)
        if (
            "FROM lexeme_occurrences o" in s
            and "an.document_id" in s
            and "sp.char_start >=" not in s
        ):
            return [
                {
                    "char_start": 14,
                    "char_end": 19,
                    "uid": LEX_UID,
                    "lemma": "world",
                    "pos": "NOUN",
                }
            ]
        return fake_q(sql, params)

    monkeypatch.setattr(webapp, "q1", html_q1)
    monkeypatch.setattr(webapp, "q", html_q)

    html = client.get(f"/doc/{DOC_ID}?view=original").get_data(as_text=True)

    assert 'class="markup-original"' in html
    assert "<h1>Heading</h1>" in html
    assert 'href="/lexeme/lx_en.v_say01" class="w p-NOUN"' in html
    assert "world · NOUN" in html
    assert "Rendered original PDF page" not in html


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
    from webapp.reader_blocks import annotate_html

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
        lambda sql, params=(): (
            []
            if "count(o.*) AS uses" in sql and "JOIN lexeme_occurrences" in sql
            else fake_q(sql, params)
        ),
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


def test_lexeme_page_computes_substring_only_over_limited_subquery(client, monkeypatch):
    """KWIC/forms must apply LIMIT before substring(full_text): computing the
    snippet for every occurrence first (105k rows for the hottest lexeme,
    measured ~10s per query live) then discarding all but 25/80 is the page's
    entire cost. The performance contract is `substring` over a bounded
    subquery only."""
    captured = []

    def recording_q(sql, params=()):
        s = _norm(sql)
        captured.append(s)
        return fake_q(sql, params)

    monkeypatch.setattr(webapp, "q", recording_q)

    assert client.get(f"/lexeme/{LEX_UID}").status_code == 200

    snippet_queries = [
        s for s in captured if "substring(" in s and ("AS kwic" in s or "AS form" in s)
    ]
    assert snippet_queries, "expected the lexeme page to build forms/KWIC snippets"
    for s in snippet_queries:
        assert "FROM (SELECT" in s, (
            "substring(full_text) must only run over a LIMITed subquery, "
            f"not over every occurrence: {s[:160]}"
        )


def test_markup_grammar_highlights_slice_surfaces_from_one_fulltext_fetch(monkeypatch):
    """The grammar overlay computed substring(full_text) per occurrence row
    (314k rows on the largest live document, measured 44.7s). Surfaces must be
    sliced in Python from one full_text fetch instead."""
    captured = []

    def recording_q(sql, params=()):
        s = _norm(sql)
        captured.append(s)
        if "FROM lexeme_occurrences o" in s:
            return [
                {"char_start": 0, "char_end": 2, "uid": "lx_en.o_he", "lemma": "he", "pos": "PRON"},
                {"char_start": 3, "char_end": 7, "uid": LEX_UID, "lemma": "say", "pos": "VERB"},
            ]
        return []

    def recording_q1(sql, params=()):
        s = _norm(sql)
        captured.append(s)
        if "full_text" in s:
            return {"full_text": "He said hello"}
        return None

    monkeypatch.setattr(webapp, "q", recording_q)
    monkeypatch.setattr(webapp, "q1", recording_q1)

    with webapp.app.test_request_context("/"):
        highlights = webapp._markup_highlights(DOC_ID, "grammar")

    assert not any("substring(" in s for s in captured), (
        "highlight surfaces must be sliced in Python from a single "
        "full_text fetch, not computed per-row in SQL"
    )
    assert [h["surface"] for h in highlights] == ["He", "said"]
    assert highlights[1]["class_name"] == "w p-VERB"


def test_apply_markup_highlights_single_surface_parity():
    html = "<p>He said hello</p>"
    highlight = {
        "surface": "said",
        "href": "/lexeme/lx_en.v_say",
        "class_name": "w p-VERB",
        "tip": "say · VERB",
        "aria_label": "say, part of speech VERB",
    }
    out = webapp._apply_markup_highlights(html, [highlight])
    assert out == (
        '<p>He <a href="/lexeme/lx_en.v_say" class="w p-VERB" '
        'data-t="say · VERB" aria-label="say, part of speech VERB">said</a> hello</p>'
    )


def test_apply_markup_highlights_wraps_repeated_surfaces_without_nesting():
    """Each occurrence of a repeated surface must get its own anchor, in
    document order. The old replace-from-the-top loop re-scanned its own
    output, so the second identical surface matched inside the first anchor's
    data-t attribute and corrupted it (and the real second occurrence was
    never wrapped)."""
    import re as _re

    html = "<p>alpha beta alpha</p>"
    highlight = {
        "surface": "alpha",
        "href": "/lexeme/lx_en.x_alpha",
        "class_name": "w p-X",
        "tip": "alpha · X",
        "aria_label": "alpha, part of speech X",
    }
    out = webapp._apply_markup_highlights(html, [highlight, dict(highlight)])

    assert out.count('data-t="alpha · X"') == 2, "both anchors must keep intact attributes"
    assert "beta <a" in out, "the second textual occurrence must be the one wrapped"
    assert not _re.search(r"<a[^>]*<a", out), "anchors must never nest into attributes"


# --- search ----------------------------------------------------------------


def test_search_highlights_the_term(client):
    html = client.get("/search?q=Winston").get_data(as_text=True)
    assert "<mark>Winston</mark>" in html
    assert "Big Brother" in html


def test_search_paginates_and_shows_next_link(client, monkeypatch):
    captured = {}
    row = {
        "doc_id": DOC_ID,
        "title": "orwell1984",
        "ordinal": 3,
        "text": "Winston loved Big Brother.",
    }

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


def test_job_status_renders_progress_bar_when_reported(client, monkeypatch):
    running_job = {
        "id": "job-running",
        "kind": "annotate",
        "status": "running",
        "document_id": DOC_ID,
        "error": None,
        "result": None,
        "created_at": datetime(2026, 7, 4, 12, 0),
        "started_at": datetime(2026, 7, 4, 12, 0, 5),
        "finished_at": None,
        "progress_current": 7,
        "progress_total": 10,
        "progress_stage": "annotating sentence batch",
    }

    def running_q(sql, params=()):
        if "FROM jobs WHERE document_id" in _norm(sql):
            return [running_job]
        return fake_q(sql, params)

    def running_q1(sql, params=()):
        if "FROM jobs WHERE id" in _norm(sql):
            return running_job
        return fake_q1(sql, params)

    monkeypatch.setattr(webapp, "q", running_q)
    monkeypatch.setattr(webapp, "q1", running_q1)

    html = client.get("/job/job-running").get_data(as_text=True)

    assert 'class="job-progress"' in html
    assert 'value="7"' in html
    assert 'max="10"' in html
    assert "70%" in html
    assert "annotating sentence batch" in html
    assert "Keep this page open for live progress." in html
    assert "Running" in html
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
    assert "Job spool" in html
    assert "attempt 3 / 3" in html
    assert "worker-7" in html
    assert "Registering token and entity spans" in html
    assert "reclaimed: stale heartbeat" in html
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
    assert set(payload["corpus"]) == {
        "totals",
        "issues",
        "issue_groups",
        "jobs",
        "job_spool",
    }
    assert set(payload["database"]) == {
        "size",
        "connections",
        "tables",
        "issues",
        "issue_groups",
    }
    assert payload["corpus"]["totals"]["documents"] == 5
    assert payload["corpus"]["job_spool"][0] == {
        "id": "job-stale",
        "kind": "annotate",
        "status": "running",
        "attempts": 3,
        "max_attempts": 3,
        "worker": "worker-7",
        "document_id": DOC_ID,
        "document_title": "orwell1984",
        "progress": {
            "current": 3,
            "total": 7,
            "stage": "Registering token and entity spans",
        },
        "runtime_seconds": 4389,
        "heartbeat_age_seconds": 14,
        "queued_age_seconds": 4389,
        "reclaim_after_seconds": 286,
        "last_error": "reclaimed: stale heartbeat",
    }
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
# BL-107 — the operator token must not have to travel via the URL query
# string on every request: a header works standalone, and a query-string
# bootstrap link sets a short-lived signed session flag so later navigation
# (internal links) no longer needs the token in the URL at all.
# ---------------------------------------------------------------------------


def test_operator_health_accepts_header_token_without_query_string(client):
    webapp.app.config["OPERATOR_HEALTH_TOKEN"] = "secret"

    resp = client.get("/operator/health", headers={"X-Operator-Token": "secret"})
    assert resp.status_code == 200


def test_operator_health_query_bootstrap_then_no_longer_needs_the_token(client):
    webapp.app.config["OPERATOR_HEALTH_TOKEN"] = "secret"

    first = client.get("/operator/health?token=secret")
    assert first.status_code == 200

    # Same client (session cookie carried over) -- no token anywhere in this request.
    second = client.get("/operator/health")
    assert second.status_code == 200


def test_operator_health_page_links_do_not_embed_the_token(client):
    webapp.app.config["OPERATOR_HEALTH_TOKEN"] = "secret"

    html = client.get("/operator/health?token=secret").get_data(as_text=True)
    assert "token=secret" not in html
    assert "?token=" not in html


def test_operator_health_issue_links_to_metadata_correction(client):
    webapp.app.config["OPERATOR_HEALTH_TOKEN"] = "secret"

    html = client.get("/operator/health?token=secret").get_data(as_text=True)

    assert f'href="/operator/doc/{DOC_ID}/metadata?issue=document-unannotated"' in html
    assert "Correct metadata" in html


def test_operator_metadata_correction_requires_operator_access(client):
    webapp.app.config["OPERATOR_HEALTH_TOKEN"] = "secret"

    assert client.get(f"/operator/doc/{DOC_ID}/metadata").status_code == 404


def test_operator_metadata_correction_form_is_prefilled_and_gated(client):
    webapp.app.config["OPERATOR_HEALTH_TOKEN"] = "secret"

    html = client.get(
        f"/operator/doc/{DOC_ID}/metadata?issue=document-unannotated&token=secret"
    ).get_data(as_text=True)

    assert "Operator correction" in html
    assert "document-unannotated" in html
    assert f'action="/operator/doc/{DOC_ID}/metadata"' in html
    assert 'name="title" value="orwell1984"' in html
    assert 'name="description"' in html
    assert 'name="tags"' in html
    assert 'name="csrf_token"' in html


def test_operator_metadata_correction_reuses_metadata_mutation(client, monkeypatch):
    webapp.app.config["OPERATOR_HEALTH_TOKEN"] = "secret"
    captured = {}

    def fake_gql(query, variables=None):
        captured["query"] = query
        captured["variables"] = variables
        return {"updateDocumentMetadata": {"id": DOC_ID}}

    monkeypatch.setattr(webapp, "gql", fake_gql)

    form_html = client.get(f"/operator/doc/{DOC_ID}/metadata?token=secret").get_data(as_text=True)
    token = _csrf_token_from(form_html)
    resp = client.post(
        f"/operator/doc/{DOC_ID}/metadata",
        data={
            "title": "Corrected title",
            "description": "Curated note",
            "tags": "ops, corrected",
            "csrf_token": token,
        },
    )

    assert resp.status_code == 302
    assert f"/operator/doc/{DOC_ID}/metadata" in resp.headers["Location"]
    assert "metadata_queued=1" in resp.headers["Location"]
    assert "updateDocumentMetadata" in captured["query"]
    assert captured["variables"] == {
        "id": DOC_ID,
        "title": "Corrected title",
        "description": "Curated note",
        "tags": ["ops", "corrected"],
    }


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


def test_login_redirects_to_oidc_provider_and_records_state(monkeypatch):
    monkeypatch.setenv("DOCRAY_WEBAPP_OIDC_AUTHORIZATION_URL", "https://idp.example/authorize")
    monkeypatch.setenv("DOCRAY_WEBAPP_OIDC_CLIENT_ID", "doc-ray-web")

    client = webapp.app.test_client()
    resp = client.get("/login")

    assert resp.status_code == 302
    assert resp.headers["Location"].startswith("https://idp.example/authorize?")
    assert "client_id=doc-ray-web" in resp.headers["Location"]
    assert "response_type=code" in resp.headers["Location"]
    with client.session_transaction() as sess:
        state = sess["_auth_state"]
    assert f"state={state}" in resp.headers["Location"]


def test_login_redirects_to_saml_provider_when_oidc_unconfigured(monkeypatch):
    monkeypatch.delenv("DOCRAY_WEBAPP_OIDC_AUTHORIZATION_URL", raising=False)
    monkeypatch.delenv("DOCRAY_WEBAPP_OIDC_CLIENT_ID", raising=False)
    monkeypatch.setenv("DOCRAY_WEBAPP_SAML_SSO_URL", "https://idp.example/saml/sso")

    client = webapp.app.test_client()
    resp = client.get("/login")

    assert resp.status_code == 302
    assert resp.headers["Location"].startswith("https://idp.example/saml/sso?")
    with client.session_transaction() as sess:
        state = sess["_auth_state"]
    assert f"RelayState={state}" in resp.headers["Location"]


def test_oidc_callback_sets_secure_server_side_session_cookie(monkeypatch):
    principal_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    webapp.auth_sessions.reset()
    monkeypatch.setenv("DOCRAY_WEBAPP_OIDC_AUTHORIZATION_URL", "https://idp.example/authorize")
    monkeypatch.setenv("DOCRAY_WEBAPP_OIDC_CLIENT_ID", "doc-ray-web")
    monkeypatch.setattr(
        webapp,
        "complete_oidc_login",
        lambda code, redirect_uri: webapp.AuthenticatedWebPrincipal(
            principal_id=principal_id,
            subject="oidc:alice",
        ),
    )

    client = webapp.app.test_client()
    client.get("/login")
    with client.session_transaction() as sess:
        state = sess["_auth_state"]
    resp = client.get(f"/auth/oidc/callback?code=abc&state={state}")

    assert resp.status_code == 302
    cookie = resp.headers["Set-Cookie"]
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    with client.session_transaction() as sess:
        sid = sess["_auth_sid"]
        assert "_auth_state" not in sess
    auth_session = webapp.auth_sessions.get(sid)
    assert auth_session is not None
    assert auth_session.principal_id == principal_id


def test_saml_callback_sets_server_side_session_without_csrf(monkeypatch):
    principal_id = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    webapp.auth_sessions.reset()
    monkeypatch.setattr(
        webapp,
        "complete_saml_login",
        lambda saml_response: webapp.AuthenticatedWebPrincipal(
            principal_id=principal_id,
            subject="saml:alice",
        ),
    )

    client = webapp.app.test_client()
    with client.session_transaction() as sess:
        sess["_auth_state"] = "relay-1"
        sess["_csrf_token"] = "csrf-1"

    resp = client.post(
        "/auth/saml/callback",
        data={"SAMLResponse": "assertion", "RelayState": "relay-1"},
    )

    assert resp.status_code == 302
    with client.session_transaction() as sess:
        sid = sess["_auth_sid"]
        assert "_auth_state" not in sess
    auth_session = webapp.auth_sessions.get(sid)
    assert auth_session is not None
    assert auth_session.principal_id == principal_id


def test_get_conn_prefers_session_principal_over_env_fallback(monkeypatch):
    fake = _RecordingConn()
    sid = webapp.auth_sessions.create(
        principal_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        subject="oidc:bob",
    )
    monkeypatch.setattr(webapp.psycopg, "connect", lambda *a, **k: fake)
    monkeypatch.setattr(webapp, "PRINCIPAL_ID", "11111111-1111-1111-1111-111111111111")
    monkeypatch.setattr(webapp, "DB_ROLE", "docray_client")

    with webapp.app.test_request_context("/"):
        session["_auth_sid"] = sid
        webapp.get_conn()

    assert any(
        "set_config" in query and params == ("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",)
        for query, params in fake.calls
    )
    assert not any(
        params == ("11111111-1111-1111-1111-111111111111",) for _query, params in fake.calls
    )


def test_expired_auth_session_revokes_without_env_fallback(monkeypatch):
    fake = _RecordingConn()
    webapp.auth_sessions.reset()
    sid = webapp.auth_sessions.create(
        principal_id="eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
        subject="oidc:expired",
        ttl_seconds=-1,
    )
    monkeypatch.setattr(webapp.psycopg, "connect", lambda *a, **k: fake)
    monkeypatch.setattr(webapp, "PRINCIPAL_ID", "11111111-1111-1111-1111-111111111111")

    with webapp.app.test_request_context("/"):
        session["_auth_sid"] = sid
        webapp.get_conn()
        assert "_auth_sid" not in session

    assert webapp.auth_sessions.get(sid) is None
    assert fake.calls == []


def test_logout_revokes_server_side_session(monkeypatch):
    principal_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    webapp.auth_sessions.reset()
    monkeypatch.setattr(
        webapp,
        "complete_oidc_login",
        lambda code, redirect_uri: webapp.AuthenticatedWebPrincipal(
            principal_id=principal_id,
            subject="oidc:carol",
        ),
    )

    client = webapp.app.test_client()
    with client.session_transaction() as sess:
        sess["_auth_state"] = "state-1"
        sess["_csrf_token"] = "csrf-1"
    client.get("/auth/oidc/callback?code=abc&state=state-1")
    with client.session_transaction() as sess:
        sid = sess["_auth_sid"]
    assert webapp.auth_sessions.get(sid) is not None

    resp = client.post("/logout", data={"csrf_token": "csrf-1"})

    assert resp.status_code == 302
    assert webapp.auth_sessions.get(sid) is None
    with client.session_transaction() as sess:
        assert "_auth_sid" not in sess


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


# ---------------------------------------------------------------------------
# Backup download proxy: forwards the API credential and propagates a denied
# upstream status as the same status instead of a proxy 500 (BL-094 — the API
# route is now admin-only, so the webapp side must carry the token through and
# handle 401/403 cleanly, not just the 404 it already knew about).
# ---------------------------------------------------------------------------


class _FakeUpstreamResponse:
    def __init__(self, status_code, chunks=(b"",), headers=None):
        self.status_code = status_code
        self._chunks = chunks
        self.headers = headers or {}

    def iter_bytes(self, chunk_size=None):
        yield from self._chunks

    def close(self):
        pass

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"upstream {self.status_code}")


def test_backups_download_forwards_the_api_token(client, monkeypatch):
    import httpx

    from webapp import graphql_client

    captured = {}

    def fake_send(_self, request, **_kwargs):
        captured["authorization"] = request.headers.get("authorization")
        return _FakeUpstreamResponse(200, chunks=(b"archive-bytes",))

    monkeypatch.setattr(httpx.Client, "send", fake_send)
    monkeypatch.setattr(graphql_client, "API_TOKEN", "webapp-token")
    resp = client.get("/backups/some-id/download")
    assert resp.status_code == 200
    assert captured["authorization"] == "Bearer webapp-token"


def test_backups_download_propagates_upstream_403_not_500(client, monkeypatch):
    import httpx

    monkeypatch.setattr(httpx.Client, "send", lambda _self, _r, **_k: _FakeUpstreamResponse(403))
    resp = client.get("/backups/some-id/download")
    assert resp.status_code == 403


def test_backups_download_propagates_upstream_401_not_500(client, monkeypatch):
    import httpx

    monkeypatch.setattr(httpx.Client, "send", lambda _self, _r, **_k: _FakeUpstreamResponse(401))
    resp = client.get("/backups/some-id/download")
    assert resp.status_code == 401


def test_document_page_links_to_original_download(client):
    html = client.get(f"/doc/{DOC_ID}").get_data(as_text=True)

    assert f'href="/doc/{DOC_ID}/download"' in html
    assert "Download original" in html


def test_document_download_proxies_original_file_from_api(client, monkeypatch):
    import httpx

    from webapp import graphql_client

    captured = {}

    def fake_send(_self, request, **_kwargs):
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        return _FakeUpstreamResponse(
            200,
            chunks=(b"original-bytes",),
            headers={
                "content-type": "application/pdf",
                "content-disposition": 'attachment; filename="Original Report.pdf"',
            },
        )

    monkeypatch.setattr(httpx.Client, "send", fake_send)
    monkeypatch.setattr(graphql_client, "API_TOKEN", "webapp-token")

    resp = client.get(f"/doc/{DOC_ID}/download")

    assert resp.status_code == 200
    assert resp.data == b"original-bytes"
    assert captured["url"].endswith(f"/documents/{DOC_ID}/download")
    assert captured["authorization"] == "Bearer webapp-token"
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers["content-disposition"] == 'attachment; filename="Original Report.pdf"'


@pytest.mark.parametrize("status", [401, 403, 404, 409])
def test_document_download_propagates_clean_upstream_errors(client, monkeypatch, status):
    import httpx

    monkeypatch.setattr(
        httpx.Client, "send", lambda _self, _request, **_kwargs: _FakeUpstreamResponse(status)
    )

    resp = client.get(f"/doc/{DOC_ID}/download")

    assert resp.status_code == status


def test_document_page_image_proxies_rendered_png_from_api(client, monkeypatch):
    import httpx

    from webapp import graphql_client

    captured = {}

    def fake_send(_self, request, **_kwargs):
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        return _FakeUpstreamResponse(
            200,
            chunks=(b"\x89PNG", b"-page"),
            headers={"content-type": "image/png", "x-docray-cache": "hit"},
        )

    monkeypatch.setattr(httpx.Client, "send", fake_send)
    monkeypatch.setattr(graphql_client, "API_TOKEN", "webapp-token")

    resp = client.get(f"/doc/{DOC_ID}/page/1.png")

    assert resp.status_code == 200
    assert resp.data == b"\x89PNG-page"
    assert captured["url"].endswith(f"/documents/{DOC_ID}/pages/1.png")
    assert captured["authorization"] == "Bearer webapp-token"
    assert resp.headers["content-type"] == "image/png"
    assert resp.headers["x-docray-cache"] == "hit"


@pytest.mark.parametrize("status", [401, 403, 404, 409, 415])
def test_document_page_image_propagates_clean_upstream_errors(client, monkeypatch, status):
    import httpx

    monkeypatch.setattr(
        httpx.Client, "send", lambda _self, _request, **_kwargs: _FakeUpstreamResponse(status)
    )

    resp = client.get(f"/doc/{DOC_ID}/page/1.png")

    assert resp.status_code == status


# --- CSRF protection (BL-103) -----------------------------------------------


def _csrf_token_from(html: str) -> str:
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m, "no csrf_token hidden field found in rendered form"
    return m.group(1)


@pytest.mark.parametrize(
    ("path", "get_path"),
    [
        ("/upload", "/upload"),
        (f"/doc/{DOC_ID}/seeding", f"/doc/{DOC_ID}/seeding"),
        (f"/doc/{DOC_ID}/reprocess", f"/doc/{DOC_ID}"),
        ("/backups", "/backups"),
        ("/backups/some-id/restore", "/backups"),
    ],
)
def test_post_without_csrf_token_is_rejected(client, monkeypatch, path, get_path):
    """A state-changing POST with no token never reaches the route body (BL-103)."""
    import httpx

    calls = []
    monkeypatch.setattr(webapp, "gql", lambda *a, **k: calls.append((a, k)) or {})
    monkeypatch.setattr(httpx, "post", lambda *a, **k: calls.append((a, k)) or None)
    client.get(get_path)  # establish a session so a missing/blank token is the only variable
    resp = client.post(path, data={})
    assert resp.status_code == 400
    assert not calls


def test_post_with_wrong_csrf_token_is_rejected(client):
    client.get("/backups")
    resp = client.post("/backups", data={"note": "x", "csrf_token": "not-the-real-token"})
    assert resp.status_code == 400


def test_post_with_valid_csrf_token_creates_backup(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        webapp, "gql", lambda *a, **k: calls.append((a, k)) or {"createBackup": {"id": "job-1"}}
    )
    html = client.get("/backups").get_data(as_text=True)
    token = _csrf_token_from(html)
    resp = client.post("/backups", data={"note": "x", "csrf_token": token})
    assert resp.status_code == 302
    assert calls


def test_post_with_valid_csrf_token_dispatches_seeding_action(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        webapp, "gql", lambda *a, **k: calls.append((a, k)) or {"suggestSeeds": {"id": "job-1"}}
    )
    html = client.get(f"/doc/{DOC_ID}/seeding").get_data(as_text=True)
    token = _csrf_token_from(html)
    resp = client.post(f"/doc/{DOC_ID}/seeding", data={"action": "mine", "csrf_token": token})
    assert resp.status_code == 302
    assert calls


@pytest.mark.parametrize(
    ("get_path", "post_path", "data", "expected_location", "expected_log"),
    [
        (
            f"/doc/{DOC_ID}/seeding",
            f"/doc/{DOC_ID}/seeding",
            {"action": "mine"},
            f"/doc/{DOC_ID}/seeding?error=permission+denied",
            "seeding action failed",
        ),
        (
            "/backups",
            "/backups",
            {"note": "nightly"},
            "/backups?error=permission+denied",
            "backup creation failed",
        ),
        (
            "/backups",
            "/backups/backup-1/restore",
            {},
            "/backups?error=permission+denied",
            "backup restore failed",
        ),
    ],
)
def test_webapp_write_error_handlers_log_before_redirecting(
    client, monkeypatch, get_path, post_path, data, expected_location, expected_log
):
    messages = []

    def raising_gql(*_args, **_kwargs):
        raise RuntimeError("permission denied")

    monkeypatch.setattr(webapp, "gql", raising_gql)
    monkeypatch.setattr(webapp.app.logger, "exception", lambda message: messages.append(message))

    html = client.get(get_path).get_data(as_text=True)
    token = _csrf_token_from(html)
    resp = client.post(post_path, data={**data, "csrf_token": token})

    assert resp.status_code == 302
    assert expected_location in resp.headers["Location"]
    assert messages == [expected_log]


# ---------------------------------------------------------------------------
# BL-104 — every response carries a baseline set of security headers.
# ---------------------------------------------------------------------------


def test_responses_carry_security_headers(client):
    resp = client.get("/")
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    csp = resp.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp


# ---------------------------------------------------------------------------
# BL-123 gap — a running job must show real elapsed time once progress
# advances, and a running job whose heartbeat went stale must read as stalled
# (and stop blocking the reprocess controls) instead of wearing "running"
# forever.
#
# No ETA: pipeline stages are not equally sized (the dominant stage for large
# documents can run minutes to hours), so a linear elapsed/remaining
# extrapolation is not "rough" — it is confidently wrong, sometimes by an
# order of magnitude (verified live: a job reporting "~5m left" whose owning
# worker container no longer existed). Elapsed time is always true; an
# extrapolated ETA from unequal stages is not.
# ---------------------------------------------------------------------------


def _dt(seconds_ago: float):
    from datetime import timedelta, timezone

    return datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)


def test_summarize_progress_reports_elapsed_time_once_running():
    from webapp.job_view import summarize_progress

    job = {
        "progress_current": 1,
        "progress_total": 4,
        "progress_stage": "Running spaCy pipeline",
        "started_at": _dt(60),
        "status": "running",
    }
    progress = summarize_progress(job)
    assert progress is not None
    assert progress["elapsed_seconds"] == pytest.approx(60, rel=0.2)
    assert progress["elapsed_label"] == "60s"
    assert "eta_seconds" not in progress
    assert "eta_label" not in progress


def test_summarize_progress_omits_elapsed_without_signal():
    from webapp.job_view import summarize_progress

    too_fresh = summarize_progress(
        {"progress_current": 0, "progress_total": 4, "started_at": _dt(1), "status": "running"}
    )
    assert too_fresh is not None and "elapsed_label" not in too_fresh
    no_start = summarize_progress({"progress_current": 2, "progress_total": 4, "status": "running"})
    assert no_start is not None and "elapsed_label" not in no_start
    finished = summarize_progress(
        {"progress_current": 4, "progress_total": 4, "started_at": _dt(60), "status": "succeeded"}
    )
    assert finished is not None and "elapsed_label" not in finished


def test_summarize_progress_flags_heartbeat_uncertain_past_reclaim_window():
    """Warn once the heartbeat is older than the backend's own reclaim-eligibility
    window (120s default) — well before is_stalled's looser 300s threshold would
    say so, matching what the backend already considers reclaimable."""
    from webapp.job_view import summarize_progress

    likely_dead = summarize_progress(
        {
            "progress_current": 1,
            "progress_total": 4,
            "started_at": _dt(200),
            "heartbeat_at": _dt(150),
            "status": "running",
        }
    )
    assert likely_dead is not None
    assert likely_dead["heartbeat_uncertain"] is True

    healthy = summarize_progress(
        {
            "progress_current": 1,
            "progress_total": 4,
            "started_at": _dt(60),
            "heartbeat_at": _dt(10),
            "status": "running",
        }
    )
    assert healthy is not None
    assert "heartbeat_uncertain" not in healthy


def test_stalled_running_job_is_flagged_and_not_active():
    from webapp.job_view import decorate_job_rows, is_active_job

    fresh = {"id": "j1", "kind": "annotate", "status": "running", "heartbeat_at": _dt(10)}
    stale = {"id": "j2", "kind": "annotate", "status": "running", "heartbeat_at": _dt(900)}
    queued = {"id": "j3", "kind": "annotate", "status": "queued", "heartbeat_at": None}

    assert is_active_job(fresh) is True
    assert is_active_job(stale) is False, "stale-heartbeat running job must not block retries"
    assert is_active_job(queued) is True

    decorated = {row["id"]: row for row in decorate_job_rows([fresh, stale, queued])}
    assert decorated["j2"]["stalled"] is True
    assert decorated["j1"]["stalled"] is False
    assert "stalled" in decorated["j2"]["status_label"].lower()


def test_created_collection_survives_the_creating_request():
    """BL-131: collection INSERTs must COMMIT, not roll back at teardown.

    The creating request redirects with a real id (RETURNING works inside the
    open transaction), then the connection closes and the row evaporates. A
    second request — a fresh connection — must still find the collection.

    Deliberately does NOT use the ``client`` fixture: that fixture fakes
    ``q``/``q1`` entirely, which is exactly why this bug shipped invisible.
    This test drives the real request/connection lifecycle against the
    database (skipped when unreachable, like the other live-DB suites).
    """
    import os

    import psycopg

    try:
        probe = psycopg.connect(os.environ.get("DATABASE_URL", ""), connect_timeout=2)
        probe.close()
    except Exception:
        pytest.skip("database unreachable")

    webapp.app.config.update(TESTING=True)
    real_client = webapp.app.test_client()
    token = _csrf_token_from(real_client.get("/collections").get_data(as_text=True))
    resp = real_client.post(
        "/collections",
        data={
            "name": "bl131-commit-proof",
            "description": "regression fixture",
            "kind": "manual",
            "csrf_token": token,
        },
    )
    assert resp.status_code == 302
    location = resp.headers["Location"]
    try:
        detail = real_client.get(location)
        assert detail.status_code == 200, (
            "collection vanished after the creating request — the INSERT was rolled back"
        )
        assert b"bl131-commit-proof" in detail.data
    finally:
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
            conn.execute("DELETE FROM document_collections WHERE name = 'bl131-commit-proof'")
