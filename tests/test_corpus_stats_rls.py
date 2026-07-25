"""Guard: the corpus stat cards stay RLS-scoped, fast, and free of tenant leaks.

Regression for a multi-tenant confidentiality leak: a user with zero visible
documents once saw the *global* lexeme count on "The Corpus" page, because the
`lexemes` table has NO row-level security (018_rbac.sql groups it with the shared
authors/tags registries).

That page also recomputed four corpus-wide aggregates on EVERY request; two of
them full-scanned the 17.5M-row `lexeme_occurrences` table (`count(DISTINCT
lexeme_uid)` and `count(*)`), costing ~18.7 s of origin time against a 300 ms
SLA (measured live 2026-07-16 via EXPLAIN ANALYZE as `docray_api` + admin).

Migration 095 fixed both at once by reading precomputed rollups on the hot path:
  * documents / sentences / occurrences — `count(*)` over documents plus SUM over
    `document_stats`, which is RLS-scoped by document_id, so the totals are exact
    for the caller's visible corpus.
  * lexemes (distinct) — a maintained PER-TENANT scalar in `corpus_stats`, gated
    on "caller sees >= 1 document" so a principal with no visible corpus reads 0.

These are static guards on the read path and the migration; the behavioural
RLS/correctness tests live in
``src/doc_ray/tests/test_corpus_stats_rollups.py`` (they need the isolated test
DB, like every other DB-backed suite).
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_CORPUS_WEB = _ROOT / "webapp" / "corpus_web.py"
_MIGRATION = _ROOT / "db" / "migrations" / "095_corpus_stats.sql"


def test_read_path_reads_precomputed_rollups_not_live_scans():
    text = _CORPUS_WEB.read_text(encoding="utf-8")
    # The stat band reads the rollups migration 095 maintains ...
    assert "FROM document_stats" in text, "sentence/occurrence totals must read document_stats"
    assert "n_distinct_lexemes FROM corpus_stats" in text, (
        "the lexeme total must read the maintained corpus_stats scalar"
    )
    # ... never the two full-table scans of lexeme_occurrences it used to do
    # (14.9 s + 3.3 s on the 17.5M-row table on every page load).
    assert "count(DISTINCT o.lexeme_uid) FROM lexeme_occurrences" not in text, (
        "the distinct-lexeme scan must not run on the read path"
    )
    assert "(SELECT count(*) FROM lexeme_occurrences) occ" not in text, (
        "the occurrence count must not scan lexeme_occurrences on the read path"
    )


def test_lexeme_card_is_visibility_gated_not_global_registry():
    text = _CORPUS_WEB.read_text(encoding="utf-8")
    # A principal that sees no documents reads 0 — the scalar is behind this gate.
    # Match the gate, not its exact text. Pinning the literal
    # "EXISTS (SELECT 1 FROM documents)" broke the moment the query grew a
    # soft-delete filter ("... FROM documents WHERE lifecycle_state <>
    # 'deleted')") — a change that strengthened the gate while failing the guard
    # meant to protect it. What matters is that the scalar is behind an EXISTS
    # over the RLS-scoped documents table, whatever else that EXISTS filters on.
    assert re.search(r"EXISTS \(SELECT 1 FROM documents\b", text), (
        "the lexeme card must be gated on the caller seeing >= 1 document"
    )
    # It must NOT count the un-scoped global `lexemes` registry as a stat total —
    # that leaks every tenant's vocabulary to a user who can see no documents.
    assert "(SELECT count(*) FROM lexemes)" not in text, (
        "bare count(*) FROM lexemes leaks the global registry across tenants"
    )


def test_migration_095_keys_the_scalar_per_tenant():
    text = _MIGRATION.read_text(encoding="utf-8")
    # The distinct-lexeme scalar is keyed per tenant, never one global row: a
    # global row would leak one tenant's vocabulary magnitude to another the
    # moment a second tenant is onboarded (docray_readable_documents() is
    # already tenant-scoped).
    assert "CREATE TABLE IF NOT EXISTS corpus_stats" in text
    assert re.search(r"tenant_id\s+uuid PRIMARY KEY", text), (
        "corpus_stats must be keyed by tenant_id (per-tenant, not one global row)"
    )
    # Its recompute is scoped to a single tenant ...
    assert "WHERE d.tenant_id = tenant" in text
    # ... and its read policy only ever exposes the caller's own tenant row.
    assert "tenant_id = docray_current_tenant()" in text
    # The summable rollup keeps the standard document-scoped RLS.
    assert "docray_attach_rls('document_stats'::regclass, 'document_id IN (%DOCS%)')" in text
