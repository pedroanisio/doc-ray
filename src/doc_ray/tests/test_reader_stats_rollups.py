"""Behaviour of the reader-page rollups + canonical grant (migrations 097-098).

Run against the live schema (skip locally without the isolated test DB, like
every other DB-backed suite).
"""

from __future__ import annotations

from pathlib import Path

from doc_ray.adapters import persistence as db

_ROOT = Path(__file__).resolve().parents[3]


def _apply(conn, *names: str) -> None:
    for name in names:
        conn.execute((_ROOT / "db" / "migrations" / name).read_text())


def _seed_doc_with_occurrences(conn) -> tuple[str, str]:
    """Create a document with two distinct lexeme occurrences; return (doc_id, tenant_id)."""
    import _itdb

    doc_id = _itdb.make_doc(conn, "Zyzzogeton zyzzogeton xylophone abounds.")
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
    tenant = conn.execute("SELECT tenant_id FROM documents WHERE id = %s", (doc_id,)).fetchone()[
        "tenant_id"
    ]
    return doc_id, str(tenant)


def test_canonical_is_readable_under_the_api_role(pg) -> None:
    """Migration 097 lets docray_api read canonical (the entity-overlay 500 fix)."""
    conn, _created = pg
    _apply(conn, "097_canonical_client_read.sql")
    conn.execute("SET ROLE docray_api")
    try:
        # This SELECT raised InsufficientPrivilege before the grant.
        conn.execute("SELECT count(*) FROM canonical").fetchone()
    finally:
        conn.execute("RESET ROLE")


def test_lexeme_stats_uses_match_live_counts(pg) -> None:
    conn, created = pg
    _apply(conn, "095_corpus_stats.sql", "098_reader_stats.sql")
    doc_id, tenant = _seed_doc_with_occurrences(conn)
    created.append(doc_id)
    conn.execute("SELECT docray_refresh_lexeme_stats(%s)", (tenant,))
    truth = conn.execute(
        """
        SELECT o.lexeme_uid, count(*) n
        FROM lexeme_occurrences o
        JOIN annotations a ON a.id = o.annotation_id
        JOIN documents d   ON d.id = a.document_id
        WHERE d.tenant_id = %s
        GROUP BY o.lexeme_uid
        """,
        (tenant,),
    ).fetchall()
    for row in truth:
        rolled = conn.execute(
            "SELECT uses FROM lexeme_stats WHERE tenant_id = %s AND lexeme_uid = %s",
            (tenant, row["lexeme_uid"]),
        ).fetchone()
        assert rolled is not None, f"missing lexeme_stats row for {row['lexeme_uid']}"
        assert rolled["uses"] == row["n"]


def test_document_stats_pos_and_verbs_match_live(pg) -> None:
    conn, created = pg
    _apply(conn, "095_corpus_stats.sql", "098_reader_stats.sql")
    doc_id, _tenant = _seed_doc_with_occurrences(conn)
    created.append(doc_id)
    conn.execute("SELECT docray_refresh_document_stats(%s)", (doc_id,))
    stats = conn.execute(
        "SELECT pos_counts, top_verbs FROM document_stats WHERE document_id = %s", (doc_id,)
    ).fetchone()
    # Two NOUN occurrences seeded, no verbs.
    pos = {row["pos"]: row["n"] for row in (stats["pos_counts"] or [])}
    assert pos.get("NOUN") == 2, f"expected 2 NOUN occurrences, got {stats['pos_counts']}"
    assert stats["top_verbs"] == [], "no verbs were seeded"
