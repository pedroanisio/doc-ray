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
    """(Re-)apply migration 095 so each test is self-contained and backfilled.

    Replaying 095 alone would leave the database *behind* the schema: it
    ``CREATE OR REPLACE``s ``docray_refresh_corpus_stats``, so any later migration
    that extends that function is silently reverted — for the rest of the pytest
    session, not just this test. Every later migration that redefines it is
    replayed in order afterwards, so this helper restores the current definition
    rather than an old one. Found when 142 added ``corpus_stats.n_sentences``:
    replaying 095 reverted the function, and the rollup then undercounted with no
    error anywhere.
    """
    migrations = _ROOT / "db" / "migrations"
    conn.execute((migrations / "095_corpus_stats.sql").read_text())
    for path in sorted(migrations.glob("*.sql")):
        if path.name <= "095_corpus_stats.sql":
            continue
        sql = path.read_text()
        if "FUNCTION docray_refresh_corpus_stats" in sql:
            conn.execute(sql)
    # Migration 114's invariant: this SECURITY DEFINER helper reads tables under
    # FORCE ROW LEVEL SECURITY, so it must run under a BYPASSRLS owner or it rolls
    # up only the rows its own execution context can see. Restated unguarded — if
    # the replay cannot restore it, that must fail loudly here rather than surface
    # as a silent undercount in whichever suite happens to read the rollup next.
    conn.execute("ALTER FUNCTION docray_refresh_corpus_stats(uuid) OWNER TO docray_batch")


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


def test_corpus_stats_n_sentences_is_the_sum_over_that_tenants_documents(pg):
    """Migration 142's rollup-from-rollup identity for the sentence total.

    The denominator the term-frequency sentence scan divides by (#39). Lives here
    because this suite re-applies the rollup migrations per test, so the schema is
    in a known state — the identity is otherwise sensitive to whichever suite last
    replayed 095.
    """
    import _itdb

    conn, created = pg
    _ensure_migration_095(conn)

    tenant = conn.execute(
        "INSERT INTO tenants (slug, display_name) VALUES (%s, %s) RETURNING id",
        (f"rollup-{_itdb.unique_sha()[:12]}", "sentence rollup"),
    ).fetchone()["id"]
    doc_id = _itdb.make_doc(conn, "Alpha one. Beta two. Gamma three.")
    created.append(doc_id)
    conn.execute("UPDATE documents SET tenant_id = %s WHERE id = %s", (tenant, doc_id))

    conn.execute("SELECT docray_refresh_corpus_stats(%s)", (tenant,))

    row = conn.execute(
        """
        SELECT (SELECT COALESCE(n_sentences, 0) FROM corpus_stats WHERE tenant_id = %(t)s)
                   AS rolled_up,
               (SELECT COALESCE(SUM(ds.n_sentences), 0)
                  FROM documents d
                  JOIN document_stats ds ON ds.document_id = d.id
                 WHERE d.tenant_id = %(t)s) AS summed
        """,
        {"t": tenant},
    ).fetchone()

    assert int(row["summed"]) == 3, "fixture should contribute exactly three sentences"
    assert int(row["rolled_up"]) == int(row["summed"])


def test_full_corpus_refresh_repairs_a_zero_sentence_denominator(pg) -> None:
    """Issue #42: migration 142 called the full refresh, which omitted n_sentences."""
    import _itdb

    conn, created = pg
    _ensure_migration_095(conn)
    tenant = conn.execute(
        "INSERT INTO tenants (slug, display_name) VALUES (%s, %s) RETURNING id",
        (f"full-refresh-{_itdb.unique_sha()[:12]}", "full refresh sentence rollup"),
    ).fetchone()["id"]
    doc_id = _itdb.make_doc(conn, "Alpha one. Beta two. Gamma three.")
    created.append(doc_id)
    conn.execute("UPDATE documents SET tenant_id = %s WHERE id = %s", (tenant, doc_id))
    conn.execute("SELECT docray_refresh_document_stats(%s)", (doc_id,))
    conn.execute("SELECT docray_refresh_corpus_stats(%s)", (tenant,))
    conn.execute("UPDATE corpus_stats SET n_sentences = 0 WHERE tenant_id = %s", (tenant,))

    try:
        conn.execute("SELECT docray_refresh_all_corpus_stats()")
        row = conn.execute(
            "SELECT n_sentences FROM corpus_stats WHERE tenant_id = %s", (tenant,)
        ).fetchone()
        assert int(row["n_sentences"]) == 3
    finally:
        conn.execute("SELECT docray_refresh_corpus_stats(%s)", (tenant,))


def test_corpus_refresh_helpers_are_security_definer_and_batch_owned(pg) -> None:
    """A later CREATE OR REPLACE must not silently restore forced-RLS undercounts."""
    conn, _created = pg
    _ensure_migration_095(conn)

    rows = conn.execute(
        """
        SELECT p.oid::regprocedure::text AS helper,
               p.prosecdef AS security_definer,
               r.rolname AS owner,
               r.rolbypassrls AS owner_bypasses_rls
          FROM pg_proc p
          JOIN pg_roles r ON r.oid = p.proowner
         WHERE p.oid IN (
             'docray_refresh_corpus_stats(uuid)'::regprocedure,
             'docray_refresh_all_corpus_stats()'::regprocedure
         )
         ORDER BY helper
        """
    ).fetchall()

    assert len(rows) == 2
    assert all(row["security_definer"] for row in rows)
    assert all(row["owner"] == "docray_batch" for row in rows)
    assert all(row["owner_bypasses_rls"] for row in rows)


def test_corrective_migration_contains_a_loud_owner_assertion() -> None:
    """The apply path must raise instead of swallowing a failed owner reassignment."""
    sql = (_ROOT / "db" / "migrations" / "146_corpus_stats_sentence_denominator.sql").read_text()

    assert "RAISE EXCEPTION" in sql
    assert "r.rolname <> 'docray_batch'" in sql
    assert "NOT r.rolbypassrls" in sql
    assert "NOT p.prosecdef" in sql
