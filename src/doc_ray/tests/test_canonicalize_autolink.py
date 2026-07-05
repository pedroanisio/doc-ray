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
