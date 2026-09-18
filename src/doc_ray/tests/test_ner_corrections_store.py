"""DB-backed tests for the NER-correction persistence + reconcile hook.

Exercises the real ``entity_mention_correction`` table (migration 115) and the
apply path against a throwaway Postgres: a correction is recorded, applied to a
document's mentions, and — the load-bearing guarantee — *survives a simulated
re-annotation* that deletes and rebuilds the ML mentions.

Skips automatically when no safe test database is reachable (see conftest).
"""

from __future__ import annotations

import pytest

_itdb = pytest.importorskip("_itdb")

from doc_ray.adapters import ner_corrections_store as store  # noqa: E402
from doc_ray.adapters import persistence as db  # noqa: E402
from doc_ray.domain.ner_corrections import Action, Correction  # noqa: E402
from doc_ray.domain.ulid import new_ulid_str  # noqa: E402


@pytest.fixture
def conn():
    c = _itdb.connect()
    try:
        yield c
    finally:
        c.rollback()
        c.close()


def _ml_mention(conn, doc_id, annotation_id, start, end, entity_type, surface):
    span_id = db.ensure_spans(conn, doc_id, [(start, end)])[(start, end)]
    conn.execute(
        "INSERT INTO entity_mention (id, annotation_id, span_id, entity_type, surface) "
        "VALUES (%s, %s, %s, %s, %s)",
        (new_ulid_str(), annotation_id, span_id, entity_type, surface),
    )


def _types_by_span(conn, annotation_id):
    rows = conn.execute(
        "SELECT sp.char_start AS s, sp.char_end AS e, em.entity_type AS t "
        "FROM entity_mention em JOIN spans sp ON sp.id = em.span_id "
        "WHERE em.annotation_id = %s",
        (annotation_id,),
    ).fetchall()
    return {(r["s"], r["e"]): r["t"] for r in rows}


def test_record_and_load_roundtrip(conn):
    doc = _itdb.make_doc(conn, "Winston lived in London.")
    corr = Correction(action=Action.RECLASSIFY, char_start=16, char_end=22, entity_type="GPE")
    store.record_correction(conn, doc, corr, rationale="London is a place", original_type="PERSON")
    loaded = store.load_corrections(conn, doc)
    assert len(loaded) == 1
    assert loaded[0].action is Action.RECLASSIFY
    assert (loaded[0].char_start, loaded[0].char_end) == (16, 22)
    assert loaded[0].entity_type == "GPE"
    assert loaded[0].surface_original_type is None


def test_surface_rule_roundtrip_and_reapplies_to_future_mentions(conn):
    doc = _itdb.make_doc(conn, "Idaho and Idaho.")
    ann1 = _itdb.make_annotation(conn, doc, "entity", "spacy/test")
    _ml_mention(conn, doc, ann1, 0, 5, "GPE", "Idaho")
    store.record_correction(
        conn,
        doc,
        Correction(
            action=Action.RECLASSIFY_SURFACE,
            char_start=None,
            char_end=None,
            entity_type="PERSON",
            surface="Idaho",
            surface_original_type="GPE",
        ),
    )
    (loaded,) = store.load_corrections(conn, doc)
    assert loaded == Correction(
        action=Action.RECLASSIFY_SURFACE,
        char_start=None,
        char_end=None,
        entity_type="PERSON",
        surface="Idaho",
        surface_original_type="GPE",
    )
    store.apply_corrections(conn, doc, ann1)
    assert _types_by_span(conn, ann1) == {(0, 5): "PERSON"}

    ann2 = _itdb.make_annotation(conn, doc, "entity", "spacy/test-run2")
    _ml_mention(conn, doc, ann2, 0, 5, "GPE", "Idaho")
    _ml_mention(conn, doc, ann2, 10, 15, "GPE", "Idaho")
    store.apply_corrections(conn, doc, ann2)
    assert _types_by_span(conn, ann2) == {(0, 5): "PERSON", (10, 15): "PERSON"}


def test_apply_reclassifies_the_matching_mention(conn):
    doc = _itdb.make_doc(conn, "Winston lived in London.")
    ann = _itdb.make_annotation(conn, doc, "entity", "spacy/test")
    _ml_mention(conn, doc, ann, 16, 22, "PERSON", "London")  # the ML got the type wrong
    store.record_correction(
        conn,
        doc,
        Correction(action=Action.RECLASSIFY, char_start=16, char_end=22, entity_type="GPE"),
    )
    applied = store.apply_corrections(conn, doc, ann)
    assert _types_by_span(conn, ann)[(16, 22)] == "GPE"
    assert any(m.entity_type == "GPE" and m.source == "human" for m in applied)


def test_correction_survives_a_real_reannotation(conn):
    doc = _itdb.make_doc(conn, "Winston lived in London.")
    ann1 = _itdb.make_annotation(conn, doc, "entity", "spacy/test")
    _ml_mention(conn, doc, ann1, 16, 22, "PERSON", "London")
    store.record_correction(
        conn,
        doc,
        Correction(action=Action.RECLASSIFY, char_start=16, char_end=22, entity_type="GPE"),
    )
    store.apply_corrections(conn, doc, ann1)
    assert _types_by_span(conn, ann1)[(16, 22)] == "GPE"

    # Simulate reannotate: a NEW annotation run rebuilds the same ML mention,
    # mislabeled again. The correction log must re-apply.
    conn.execute("DELETE FROM entity_mention WHERE annotation_id = %s", (ann1,))
    ann2 = _itdb.make_annotation(conn, doc, "entity", "spacy/test-run2")
    _ml_mention(conn, doc, ann2, 16, 22, "PERSON", "London")
    store.apply_corrections(conn, doc, ann2)
    assert _types_by_span(conn, ann2)[(16, 22)] == "GPE"  # fix persisted across reannotate


def test_reject_removes_and_add_inserts(conn):
    doc = _itdb.make_doc(conn, "The year 1984 in Oceania.")
    ann = _itdb.make_annotation(conn, doc, "entity", "spacy/test")
    _ml_mention(conn, doc, ann, 9, 13, "DATE", "1984")  # spurious per the reviewer
    store.record_correction(conn, doc, Correction(action=Action.REJECT, char_start=9, char_end=13))
    store.record_correction(
        conn,
        doc,
        Correction(
            action=Action.ADD, char_start=17, char_end=24, entity_type="GPE", surface="Oceania"
        ),
    )
    store.apply_corrections(conn, doc, ann)
    spans = _types_by_span(conn, ann)
    assert (9, 13) not in spans  # rejected
    assert spans.get((17, 24)) == "GPE"  # added


def test_apply_with_no_corrections_is_a_noop(conn):
    doc = _itdb.make_doc(conn, "Nothing to fix here.")
    ann = _itdb.make_annotation(conn, doc, "entity", "spacy/test")
    _ml_mention(conn, doc, ann, 0, 7, "MISC", "Nothing")
    before = _types_by_span(conn, ann)
    store.apply_corrections(conn, doc, ann)
    assert _types_by_span(conn, ann) == before


def test_apply_to_document_resolves_the_annotation_itself(conn):
    # The pipeline hook does not know the annotation id — it applies at the
    # document level, resolving whichever annotation holds the NER mentions.
    doc = _itdb.make_doc(conn, "Winston lived in London.")
    ann = _itdb.make_annotation(conn, doc, "entity", "spacy/test")
    _ml_mention(conn, doc, ann, 16, 22, "PERSON", "London")
    store.record_correction(
        conn,
        doc,
        Correction(action=Action.RECLASSIFY, char_start=16, char_end=22, entity_type="GPE"),
    )
    changed = store.apply_corrections_to_document(conn, doc)
    assert changed is True
    assert _types_by_span(conn, ann)[(16, 22)] == "GPE"


def test_apply_to_document_is_a_noop_without_corrections(conn):
    doc = _itdb.make_doc(conn, "Nothing to fix.")
    ann = _itdb.make_annotation(conn, doc, "entity", "spacy/test")
    _ml_mention(conn, doc, ann, 0, 7, "MISC", "Nothing")
    assert store.apply_corrections_to_document(conn, doc) is False
