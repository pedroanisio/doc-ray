"""Guards: reader pages read precomputed rollups, not live lexeme_occurrences.

The document reader (/doc) and Lexicon (/lexemes) used to aggregate or scan the
17.5M-row lexeme_occurrences table on every request (measured live: Lexicon ~37 s,
reader sidebar ~1.2 s, original-page overlay ~0.5-1.7 s). Migrations 097-098 fixed
this; these static guards keep the read paths off the live-aggregation pattern.
The behavioural/correctness tests live in
``src/doc_ray/tests/test_reader_stats_rollups.py`` (they need the isolated DB).
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_CORPUS = (_ROOT / "webapp" / "corpus_web.py").read_text(encoding="utf-8")
_DOCUMENT = (_ROOT / "webapp" / "document_web.py").read_text(encoding="utf-8")
_OVERLAY = (_ROOT / "webapp" / "overlay_web.py").read_text(encoding="utf-8")
_BLOCKS = (_ROOT / "webapp" / "reader_blocks.py").read_text(encoding="utf-8")
_MIG_097 = (_ROOT / "db" / "migrations" / "097_canonical_client_read.sql").read_text(
    encoding="utf-8"
)
_MIG_098 = (_ROOT / "db" / "migrations" / "098_reader_stats.sql").read_text(encoding="utf-8")


def test_lexicon_reads_lexeme_stats_rollup_not_live_group_by():
    assert "FROM lexeme_stats ls" in _CORPUS, "Lexicon must read the lexeme_stats rollup"
    # The old live per-lexeme aggregation over lexeme_occurrences must be gone.
    assert "count(o.*) AS uses" not in _CORPUS
    assert not re.search(r"FROM lexemes l\s+JOIN lexeme_occurrences o", _CORPUS), (
        "Lexicon must not join lexeme_occurrences to count uses live"
    )


def test_document_sidebar_reads_pos_top_verbs_from_rollup():
    assert "pos_counts, top_verbs FROM document_stats" in _DOCUMENT
    # No live POS / top-verb GROUP BY over the document's occurrences.
    assert "GROUP BY l.pos ORDER BY n DESC" not in _DOCUMENT
    assert "AND l.pos = 'VERB'" not in _DOCUMENT


def test_original_view_skips_sentence_block_annotation():
    # attach_page_blocks (sentence blocks) is only rendered in the reading views,
    # never the original-image overlay — it must be guarded out there.
    assert re.search(r"if not original_view:\s*\n\s*attach_page_blocks\(", _DOCUMENT), (
        "attach_page_blocks must be skipped when original_view is set"
    )


def test_page_overlays_drive_from_spans_by_document_and_page():
    # Both overlay builders start from spans (indexed by document_id, page_number)
    # instead of materialising every occurrence of the document first.
    assert re.search(r"FROM spans sp\s+JOIN lexeme_occurrences o ON o\.span_id = sp\.id", _OVERLAY)
    assert re.search(r"FROM spans sp\s+JOIN entity_mention em ON em\.span_id = sp\.id", _OVERLAY)
    assert "sp.document_id = %s" in _OVERLAY


def test_attach_page_blocks_drives_from_spans():
    assert re.search(r"FROM spans sp\s+JOIN lexeme_occurrences o ON o\.span_id = sp\.id", _BLOCKS)
    assert "sp.document_id = %s AND sp.char_start >= %s" in _BLOCKS


def test_migration_097_grants_canonical_to_client():
    assert "GRANT SELECT ON canonical TO docray_client" in _MIG_097


def test_migration_098_adds_tenant_scoped_lexeme_stats_and_doc_columns():
    assert "CREATE TABLE IF NOT EXISTS lexeme_stats" in _MIG_098
    assert "idx_lexeme_stats_tenant_uses" in _MIG_098
    # Per-tenant read policy (never a global row that leaks vocabulary cross-tenant).
    assert "tenant_id = docray_current_tenant()" in _MIG_098
    assert "ADD COLUMN IF NOT EXISTS pos_counts" in _MIG_098
    assert "ADD COLUMN IF NOT EXISTS top_verbs" in _MIG_098
