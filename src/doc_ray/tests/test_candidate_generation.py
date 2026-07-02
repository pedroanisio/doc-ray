"""Stage-2 candidate blocking tests (live DB, skip w/o DB)."""

from __future__ import annotations

from pathlib import Path

import pytest

from doc_ray.adapters import canonical_store as store
from doc_ray.application.candidate_generation import candidates_for_form
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
        conn.execute((_MIGRATIONS / name).read_text())
    return conn


def _seed(conn, preferred, entity_type, forms, wikidata_id):
    cid = store.create_canonical(
        conn, preferred=preferred, entity_type=entity_type, wikidata_id=wikidata_id
    )
    for f in forms:
        store.upsert_form(
            conn, form_norm=form_norm(f), form_surface=f, canonical_id=cid, form_type="name"
        )
    return cid


def test_unique_high_confidence_autolinks():
    conn = _connect_or_skip()
    created: list[int] = []
    try:
        cid = _seed(conn, "Berlin", "PLACE", ["Berlin"], "Q-cg-berlin")
        created.append(cid)
        blk = candidates_for_form(conn, "berlin")  # form_norm folds case
        assert blk.auto_link_id == cid
        assert len(blk.candidates) == 1
    finally:
        for c in created:
            conn.execute("DELETE FROM canonical WHERE id = %s", (c,))
        conn.close()


def test_ambiguous_does_not_autolink():
    conn = _connect_or_skip()
    created: list[int] = []
    try:
        a = _seed(conn, "Acme Corp", "ORG", ["Acme"], "Q-cg-acme1")
        b = _seed(conn, "Acme Inc", "ORG", ["Acme"], "Q-cg-acme2")
        created += [a, b]
        blk = candidates_for_form(conn, "Acme")
        assert blk.auto_link_id is None
        # Inclusion, not equality: the shared live registry may legitimately hold
        # other canonicals whose aliases normalize to 'acme' (live example: the
        # narratology 'climax' Q569250 carries the alias 'acmé'). Extra
        # candidates only reinforce the ambiguity this test asserts.
        assert {c.canonical_id for c in blk.candidates} >= {a, b}
    finally:
        for c in created:
            conn.execute("DELETE FROM canonical WHERE id = %s", (c,))
        conn.close()


def test_novel_form_has_no_candidates():
    conn = _connect_or_skip()
    try:
        blk = candidates_for_form(conn, "zzz-nonexistent-form-xyz")
        assert blk.candidates == []
        assert blk.auto_link_id is None
    finally:
        conn.close()
