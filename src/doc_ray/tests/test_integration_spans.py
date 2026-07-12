"""Integration tests for migration 007: shared spans + layer-aware annotations.

These run against a live Postgres (DATABASE_URL, default localhost:5455) and are
skipped cleanly if it is unreachable. Each test builds a throwaway document and
deletes it afterwards (cascade), so the corpus is left untouched.
"""

from __future__ import annotations

import hashlib
import os

import _itdb
import pytest

from doc_ray.adapters import annotation as annotate
from doc_ray.adapters import persistence as db
from doc_ray.domain import lexemes as lex
from doc_ray.domain.sentences import build_sentences


def _unique_sha() -> str:
    return hashlib.sha256(os.urandom(16)).hexdigest()


def _columns(conn, table: str) -> set[str]:
    return {
        r["column_name"]
        for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name=%s",
            (table,),
        ).fetchall()
    }


@pytest.fixture
def conn():
    try:
        c = _itdb.connect()
    except _itdb.UnsafeTestDatabaseError:
        raise
    except Exception as e:  # pragma: no cover - env dependent
        pytest.skip(f"DB unreachable: {e}")
    yield c
    c.close()


@pytest.fixture
def doc(conn):
    text = "Alpha beta gamma. Delta epsilon zeta. Eta theta iota."
    sents = build_sentences(text)
    doc_id = db.insert_document(
        conn,
        title="itest",
        doc_type="txt",
        source_uri="s3://itest",
        content_sha256=_unique_sha(),
        byte_size=len(text),
        page_count=None,
        full_text=text,
        status="extracted",
        sentences=sents,
    )
    yield {"id": doc_id, "text": text, "sentences": sents}
    conn.execute("DELETE FROM documents WHERE id=%s", (doc_id,))


# --- schema shape -----------------------------------------------------------


def test_spans_table_has_natural_key(conn):
    assert "spans" in _columns(conn, "spans") or True  # table exists
    cols = _columns(conn, "spans")
    assert {"id", "document_id", "char_start", "char_end"} <= cols


def test_sentences_offsets_moved_to_spans(conn):
    cols = _columns(conn, "sentences")
    assert "span_id" in cols
    assert "char_start" not in cols and "char_end" not in cols


def test_annotations_are_layer_aware(conn):
    cols = _columns(conn, "annotations")
    assert {"layer", "tool", "tool_version"} <= cols
    assert "analyzer" not in cols


def test_occurrences_reference_span_not_offsets(conn):
    cols = _columns(conn, "lexeme_occurrences")
    assert "span_id" in cols
    assert "char_start" not in cols and "char_end" not in cols


# --- behavior ---------------------------------------------------------------


def test_sentences_get_spans_and_offsets_recover(conn, doc):
    rows = conn.execute(
        "SELECT s.text, sp.char_start, sp.char_end "
        "FROM sentences s JOIN spans sp ON sp.id=s.span_id "
        "WHERE s.document_id=%s ORDER BY s.ordinal",
        (doc["id"],),
    ).fetchall()
    assert len(rows) == len(doc["sentences"])
    for r in rows:
        assert doc["text"][r["char_start"] : r["char_end"]].split() == r["text"].split()


def test_span_natural_key_dedupes(conn, doc):
    # A document never has two span rows for the same (start,end).
    dupes = conn.execute(
        "SELECT count(*) n FROM ("
        "  SELECT char_start, char_end FROM spans WHERE document_id=%s "
        "  GROUP BY char_start, char_end HAVING count(*) > 1) t",
        (doc["id"],),
    ).fetchone()["n"]
    assert dupes == 0


def test_occurrences_map_through_spans(conn, doc):
    annotate.annotate_document(conn, doc["id"], lex.RuleAnalyzer(), {}, "en")
    rows = conn.execute(
        "SELECT sp.char_start, sp.char_end "
        "FROM lexeme_occurrences o "
        "JOIN annotations a ON a.id=o.annotation_id "
        "JOIN spans sp ON sp.id=o.span_id "
        "WHERE a.document_id=%s ORDER BY sp.char_start LIMIT 5",
        (doc["id"],),
    ).fetchall()
    assert rows, "no occurrences created"
    for r in rows:
        surface = doc["text"][r["char_start"] : r["char_end"]]
        assert surface.strip() and " " not in surface  # a single word span


def test_annotation_run_is_layer_scoped(conn, doc):
    annotate.annotate_document(conn, doc["id"], lex.RuleAnalyzer(), {}, "en")
    row = conn.execute(
        "SELECT layer, tool FROM annotations WHERE document_id=%s", (doc["id"],)
    ).fetchone()
    assert row["layer"] == "lexeme"
    assert row["tool"] == "rule/en-approx"
