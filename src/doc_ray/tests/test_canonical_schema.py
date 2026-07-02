"""Integration: the canonicalization migrations apply and round-trip (skip w/o DB).

Applies 021-023 idempotently, then inserts a canonical entity + a remissive-index form
and looks it back up by its normalized key.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from doc_ray.domain.canonical_norm import form_norm

_MIGRATIONS = Path(__file__).resolve().parents[3] / "db" / "migrations"
_FILES = ("021_canonical.sql", "022_form_index.sql", "023_resolution.sql")


def _connect_or_skip():
    import _itdb

    try:
        conn = _itdb.connect()
    except Exception as exc:  # pragma: no cover - env dependent
        pytest.skip(f"DB unreachable: {exc}")
    for name in _FILES:
        conn.execute((_MIGRATIONS / name).read_text())  # idempotent
    return conn


def test_tables_exist_and_form_roundtrips():
    conn = _connect_or_skip()
    created: list[int] = []
    try:
        for table in ("canonical", "form_index", "resolution_review", "canonical_merge"):
            reg = conn.execute("SELECT to_regclass(%s) AS t", (table,)).fetchone()["t"]
            assert reg is not None, f"{table} missing"

        cid = conn.execute(
            "INSERT INTO canonical (preferred, entity_type, description) "
            "VALUES (%s, %s, %s) RETURNING id",
            ("Test Entity", "PERSON", "unit test"),
        ).fetchone()["id"]
        created.append(cid)

        surface = "Tëst Entity"
        conn.execute(
            "INSERT INTO form_index (form_norm, form_surface, canonical_id, form_type) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (form_norm(surface), surface, cid, "name"),
        )
        rows = conn.execute(
            "SELECT canonical_id FROM form_index WHERE form_norm = %s",
            (form_norm(surface),),
        ).fetchall()
        assert any(r["canonical_id"] == cid for r in rows)
    finally:
        for cid in created:
            conn.execute("DELETE FROM canonical WHERE id = %s", (cid,))  # cascades form_index
        conn.close()
