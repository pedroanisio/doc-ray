"""Stage-2 document auto-link pass (live DB, skip w/o DB).

Seeds one entity, builds a document with a matching mention and a novel one, then asserts
the matching mention is auto-linked (entity_link scheme='canonical') and the novel one
falls to the residue.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from doc_ray.adapters import canonical_store as store
from doc_ray.application.canonicalize import autolink_document
from doc_ray.domain.canonical_norm import form_norm
from doc_ray.domain.ulid import new_ulid_str

_MIGRATIONS = Path(__file__).resolve().parents[3] / "db" / "migrations"
_FILES = ("021_canonical.sql", "022_form_index.sql", "023_resolution.sql")
_PLACE = "Autolinkberg"
_TEXT = f"Elena founded a startup in {_PLACE}."
_PLACE_START = _TEXT.index(_PLACE)
_PLACE_END = _PLACE_START + len(_PLACE)


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    """Records executed SQL; serves canned full_text and mention rows (no DB)."""

    def __init__(self, full_text, mention_rows):
        self._full_text = full_text
        self._mention_rows = mention_rows
        self.statements = []

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        self.statements.append(s)
        if s.startswith("SELECT full_text FROM document_content"):
            return _FakeCursor([{"full_text": self._full_text}])
        if "FROM entity_mention" in s:
            return _FakeCursor([dict(r) for r in self._mention_rows])
        raise AssertionError(f"unexpected query: {s}")


def test_mentions_for_document_slices_surfaces_in_python_not_sql():
    """Same anti-pattern as semantic_enrichment._tokens: substring(full_text)
    per mention row detoasts the whole document text once per mention."""
    rows = [
        {
            "mention_id": "m1",
            "entity_type": "PLACE",
            "char_start": _PLACE_START,
            "char_end": _PLACE_END,
        },
    ]
    conn = _FakeConn(_TEXT, rows)

    result = store.mentions_for_document(conn, "doc-1")

    assert not any("substring(" in s.lower() for s in conn.statements), (
        "mention surfaces must be sliced in Python from a single full_text "
        "fetch, not computed per-row in SQL"
    )
    assert result[0]["surface"] == _PLACE
    assert result[0]["mention_id"] == "m1"
    assert result[0]["entity_type"] == "PLACE"
    assert (result[0]["char_start"], result[0]["char_end"]) == (_PLACE_START, _PLACE_END)


def _connect_or_skip():
    import _itdb

    try:
        conn = _itdb.connect()
    except Exception as exc:  # pragma: no cover - env dependent
        pytest.skip(f"DB unreachable: {exc}")
    for name in _FILES:
        conn.execute((_MIGRATIONS / name).read_text())
    return conn


def test_autolink_links_known_and_leaves_residue():
    import _itdb

    conn = _connect_or_skip()
    docs: list[str] = []
    canon: list[int] = []
    try:
        cid = store.create_canonical(
            conn, preferred=_PLACE, entity_type="PLACE", wikidata_id="Q-al-autolinkberg"
        )
        canon.append(cid)
        store.upsert_form(
            conn,
            form_norm=form_norm(_PLACE),
            form_surface=_PLACE,
            canonical_id=cid,
            form_type="name",
        )

        doc_id = _itdb.make_doc(conn, _TEXT)
        docs.append(doc_id)
        aid = _itdb.make_annotation(conn, doc_id, "ner", "test")
        berlin_span = _itdb.span_of(conn, doc_id, _PLACE_START, _PLACE_END)
        elena_span = _itdb.span_of(conn, doc_id, 0, 5)
        berlin_m, elena_m = new_ulid_str(), new_ulid_str()
        conn.execute(
            "INSERT INTO entity_mention (id, annotation_id, span_id, entity_type, surface) "
            "VALUES (%s, %s, %s, 'GPE', %s)",
            (berlin_m, aid, berlin_span, _PLACE),
        )
        conn.execute(
            "INSERT INTO entity_mention (id, annotation_id, span_id, entity_type, surface) "
            "VALUES (%s, %s, %s, 'PER', %s)",
            (elena_m, aid, elena_span, _TEXT[0:5]),
        )

        result = autolink_document(conn, doc_id)
        assert result.linked == 1
        assert store.canonical_for_mention(conn, berlin_m) == cid
        assert store.canonical_for_mention(conn, elena_m) is None
        assert any(r.surface == "Elena" for r in result.residue)
    finally:
        for d in docs:
            conn.execute("DELETE FROM documents WHERE id = %s", (d,))
        for c in canon:
            conn.execute("DELETE FROM canonical WHERE id = %s", (c,))
        conn.close()
