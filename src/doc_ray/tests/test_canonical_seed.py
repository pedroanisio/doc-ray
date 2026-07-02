"""Stage-0 seed against a live DB with a stub KnowledgeBase (skip w/o DB, no network).

Exercises canonical_seed + canonical_store: a stub KB yields an entity with aliases; the
seed writes a canonical row + remissive-index forms; lookup finds it; re-seeding the same
Wikidata id is idempotent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from doc_ray.adapters import canonical_store as store
from doc_ray.application import canonical_seed
from doc_ray.domain.canonical_norm import form_norm
from doc_ray.ports.knowledge_base import KbEntry

_MIGRATIONS = Path(__file__).resolve().parents[3] / "db" / "migrations"
_FILES = ("021_canonical.sql", "022_form_index.sql", "023_resolution.sql")


class _StubKB:
    """In-memory KnowledgeBase for tests (no network)."""

    def __init__(self, entries: list[KbEntry]) -> None:
        self._by_id = {e.kb_id: e for e in entries}

    def get(self, kb_id):
        return self._by_id.get(kb_id)

    def search(self, label, entity_type=None, *, limit=10):
        return [e for e in self._by_id.values() if label.lower() in e.label.lower()][:limit]


def _connect_or_skip():
    import _itdb

    try:
        conn = _itdb.connect()
    except Exception as exc:  # pragma: no cover - env dependent
        pytest.skip(f"DB unreachable: {exc}")
    for name in _FILES:
        conn.execute((_MIGRATIONS / name).read_text())  # idempotent
    return conn


def test_seed_writes_canonical_and_forms_idempotently():
    conn = _connect_or_skip()
    created: list[int] = []
    try:
        entry = KbEntry(
            kb_id="Q-test-seed-001",
            label="Volodymyr Zelensky",
            entity_type="PERSON",
            description="president",
            lang="en",
            aliases=("Zelenskyy", "Zelenskiy"),
        )
        kb = _StubKB([entry])

        ids = canonical_seed.seed_from_ids(conn, kb, ["Q-test-seed-001"])
        assert len(ids) == 1
        cid = ids[0]
        created.append(cid)

        # Idempotent: re-seeding the same Wikidata id reuses the row.
        assert canonical_seed.seed_from_ids(conn, kb, ["Q-test-seed-001"]) == [cid]

        # form -> canonical: the alias resolves to our entity.
        rows = store.lookup_form(conn, form_norm("Zelenskyy"))
        assert any(r["canonical_id"] == cid for r in rows)

        # canonical -> forms: name + aliases registered.
        forms = store.forms_for(conn, cid)
        assert {f["form_type"] for f in forms} >= {"name", "alias"}
        assert any(f["form_surface"] == "Zelenskiy" for f in forms)
    finally:
        for cid in created:
            conn.execute("DELETE FROM canonical WHERE id = %s", (cid,))  # cascades form_index
        conn.close()
