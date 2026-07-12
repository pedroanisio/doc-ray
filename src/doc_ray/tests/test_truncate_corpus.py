"""Invariants for the ``make truncate-db`` wipe list (scripts/truncate_corpus.py).

The wipe must stay total over the corpus FK graph (TRUNCATE without CASCADE
fails otherwise) and must never swallow a preserved table. Runs against the
live schema so a new migration that adds a table forces a classification.
"""

from __future__ import annotations

import truncate_corpus as tc


def test_wiped_and_preserved_do_not_overlap():
    assert not set(tc.WIPED_TABLES) & set(tc.PRESERVED_TABLES)


def test_every_public_table_is_classified(pg):
    conn, _created = pg
    tables = {
        r["tablename"]
        for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public'"
        ).fetchall()
    }
    unclassified = tables - set(tc.WIPED_TABLES) - set(tc.PRESERVED_TABLES)
    assert not unclassified, (
        f"new table(s) need a wipe-or-preserve decision in scripts/truncate_corpus.py: "
        f"{sorted(unclassified)}"
    )


def test_no_preserved_table_references_a_wiped_table(pg):
    """TRUNCATE (no CASCADE) refuses if a table outside the list references one inside."""
    conn, _created = pg
    rows = conn.execute(
        """
        SELECT DISTINCT src.relname AS referencing, dst.relname AS referenced
        FROM pg_constraint c
        JOIN pg_class src ON src.oid = c.conrelid
        JOIN pg_class dst ON dst.oid = c.confrelid
        WHERE c.contype = 'f'
        """
    ).fetchall()
    wiped = set(tc.WIPED_TABLES)
    offenders = [
        (r["referencing"], r["referenced"])
        for r in rows
        if r["referenced"] in wiped and r["referencing"] not in wiped
    ]
    assert not offenders, (
        f"tables outside the wipe list hold FKs into it (TRUNCATE would fail): {offenders}"
    )
