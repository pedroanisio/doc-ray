"""Behaviour of the corpus stat-band rollups (migration 095).

Migration 095 replaced the four live corpus-wide aggregates "The Corpus" landing
page (webapp/corpus_web.py) recomputed on every request — two of which
full-scanned the 17.5M-row lexeme_occurrences table (~18.7 s vs. a 300 ms SLA) —
with precomputed rollups:
  * document_stats — per-document sentence/occurrence counts, RLS-scoped by
    document_id, summed on the read path (exact per principal).
  * corpus_stats — the non-summable distinct-lexeme count as a maintained
    per-tenant scalar, visibility-gated to 0 for a principal that sees no docs.

These run against the live schema (skip locally without the isolated test DB,
like every other DB-backed suite).
"""

from __future__ import annotations

import uuid
from pathlib import Path

from doc_ray.adapters import persistence as db

_ROOT = Path(__file__).resolve().parents[3]

# Mirrors the totals query in webapp/corpus_web.documents(); the read-path shape
# is pinned by the static guards in tests/test_corpus_stats_rls.py.
_TOTALS = (
    "SELECT (SELECT count(*) FROM documents) docs, "
    "(SELECT coalesce(sum(n_sentences), 0) FROM document_stats) sents, "
    "(SELECT CASE WHEN EXISTS (SELECT 1 FROM documents) "
    "             THEN coalesce((SELECT n_distinct_lexemes FROM corpus_stats), 0) "
    "             ELSE 0 END) lexemes, "
    "(SELECT coalesce(sum(n_lexeme_occurrences), 0) FROM document_stats) occ"
)


def _ensure_migration_095(conn) -> None:
    """(Re-)apply migration 095 so each test is self-contained and backfilled."""
    conn.execute((_ROOT / "db" / "migrations" / "095_corpus_stats.sql").read_text())


def _totals_as(conn, principal_id: str) -> dict:
    """Run the landing-page totals query as *principal_id* under the API role.

    Mirrors the webapp's apply_principal path: docray_current_principal() honours
    app.principal_id only under docray_api (migration 030), so a logged-in
    browser/API read binds identity this way.
    """
    conn.execute("SET ROLE docray_api")
    try:
        conn.execute("SELECT set_config('app.principal_id', %s, false)", (principal_id,))
        return conn.execute(_TOTALS).fetchone()
    finally:
        conn.execute("SELECT set_config('app.principal_id', '', false)")
        conn.execute("RESET ROLE")


def _admin_principal(conn, display_name: str) -> str:
    """Create an admin principal in the default tenant (so it can see the corpus)."""
    row = conn.execute(
        "INSERT INTO principals (kind, display_name, tenant_id) "
        "VALUES ('service', %s, docray_default_tenant_id()) RETURNING id",
        (display_name,),
    ).fetchone()
    principal_id = str(row["id"])
    conn.execute(
        "INSERT INTO principal_roles (principal_id, role_id) "
        "SELECT %s, id FROM roles WHERE name = 'admin' ON CONFLICT DO NOTHING",
        (principal_id,),
    )
    return principal_id


def test_principal_with_no_visible_corpus_reads_all_zeros(pg) -> None:
    """The confidentiality guarantee: no visible docs => every stat reads 0.

    This is the regression the leak fix defends — the lexeme card once showed the
    *global* registry count to a principal that could see no documents.
    """
    conn, _created = pg
    _ensure_migration_095(conn)
    nobody = conn.execute(
        "INSERT INTO principals (kind, display_name, tenant_id) "
        "VALUES ('human', %s, docray_default_tenant_id()) RETURNING id",
        (f"corpus-stats-nobody-{uuid.uuid4()}",),
    ).fetchone()["id"]
    try:
        row = _totals_as(conn, str(nobody))
        assert row["docs"] == 0
        assert int(row["sents"]) == 0
        assert int(row["occ"]) == 0
        assert row["lexemes"] == 0, (
            "a principal with no visible corpus must never see a lexeme count"
        )
    finally:
        conn.execute("DELETE FROM principals WHERE id = %s", (nobody,))


def test_document_stats_matches_live_sentence_count(pg) -> None:
    """docray_refresh_document_stats keeps the per-document rollup exact."""
    import _itdb

    conn, created = pg
    _ensure_migration_095(conn)
    doc_id = _itdb.make_doc(conn, "One sentence here. And then a second one.")
    created.append(doc_id)
    conn.execute("SELECT docray_refresh_document_stats(%s)", (doc_id,))
    truth = conn.execute(
        "SELECT count(*) n FROM sentences WHERE document_id = %s", (doc_id,)
    ).fetchone()["n"]
    rolled = conn.execute(
        "SELECT n_sentences FROM document_stats WHERE document_id = %s", (doc_id,)
    ).fetchone()["n_sentences"]
    assert truth > 0
    assert rolled == truth


def test_rollups_equal_live_counts_and_gate_opens_for_admin(pg) -> None:
    """After a refresh the rollups equal the live tenant counts, and the lexeme
    gate opens (non-zero) for a principal that can see the corpus."""
    import _itdb

    conn, created = pg
    _ensure_migration_095(conn)
    admin = _admin_principal(conn, f"corpus-stats-admin-{uuid.uuid4()}")
    doc_id = _itdb.make_doc(conn, "Zyzzogeton zyzzogeton xylophone abounds.")
    created.append(doc_id)
    annotation_id = _itdb.make_annotation(conn, doc_id, "lexeme", "test")
    span_a = db.ensure_spans(conn, doc_id, [(0, 10)])[(0, 10)]
    span_b = db.ensure_spans(conn, doc_id, [(22, 31)])[(22, 31)]
    lx1, _ = db.get_or_create_lexeme(conn, {}, "zyzzogeton", "NOUN", "en")
    lx2, _ = db.get_or_create_lexeme(conn, {}, "xylophone", "NOUN", "en")
    conn.execute(
        "INSERT INTO lexeme_occurrences (annotation_id, span_id, lexeme_uid) "
        "VALUES (%s, %s, %s), (%s, %s, %s)",
        (annotation_id, span_a, lx1, annotation_id, span_b, lx2),
    )
    conn.execute("SELECT docray_refresh_all_corpus_stats()")

    tenant = conn.execute("SELECT tenant_id FROM documents WHERE id = %s", (doc_id,)).fetchone()[
        "tenant_id"
    ]
    truth = conn.execute(
        "SELECT "
        "(SELECT count(*) FROM sentences s JOIN documents d ON d.id = s.document_id "
        "   WHERE d.tenant_id = %(t)s) sents, "
        "(SELECT count(*) FROM lexeme_occurrences lo JOIN annotations a ON a.id = lo.annotation_id "
        "   JOIN documents d ON d.id = a.document_id WHERE d.tenant_id = %(t)s) occ, "
        "(SELECT count(DISTINCT lo.lexeme_uid) FROM lexeme_occurrences lo "
        "   JOIN annotations a ON a.id = lo.annotation_id "
        "   JOIN documents d ON d.id = a.document_id WHERE d.tenant_id = %(t)s) lex",
        {"t": tenant},
    ).fetchone()

    try:
        row = _totals_as(conn, admin)
        assert int(row["sents"]) == truth["sents"]
        assert int(row["occ"]) == truth["occ"]
        assert row["lexemes"] == truth["lex"]
        assert row["lexemes"] > 0, "the lexeme gate must open for a principal with a visible corpus"
    finally:
        conn.execute("DELETE FROM principals WHERE id = %s", (admin,))
