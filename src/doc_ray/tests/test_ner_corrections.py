"""Block A — NER correction domain: the reconcile invariant.

These tests define the load-bearing behavior of human NER corrections: a
correction is keyed by a stable span identity and is *re-applied* onto the
freshly-produced ML mentions on every annotation run, so a fix survives
re-annotation (entity_mention rows are ephemeral — CASCADE-deleted and rebuilt
by reannotate). The reconcile function is pure and deterministic; this is where
the correctness of the whole feature lives, so it is tested without a database.
"""

from __future__ import annotations

import pytest

from doc_ray.domain.ner_corrections import (
    Action,
    Correction,
    Mention,
    reconcile,
)


def _ml(*triples):
    """Build a list of ML mentions from (start, end, type[, surface]) tuples."""
    out = []
    for t in triples:
        start, end, typ = t[0], t[1], t[2]
        surface = t[3] if len(t) > 3 else "x"
        out.append(Mention(char_start=start, char_end=end, entity_type=typ, surface=surface))
    return out


# --- reclassify ------------------------------------------------------------


def test_reclassify_changes_the_type_of_the_matching_span_and_marks_it_human():
    ml = _ml((10, 20, "PERSON", "London"))
    corr = [Correction(action=Action.RECLASSIFY, char_start=10, char_end=20, entity_type="GPE")]
    out = reconcile(ml, corr)
    assert len(out) == 1
    assert out[0].entity_type == "GPE"
    assert out[0].char_start == 10 and out[0].char_end == 20
    assert out[0].source == "human"


def test_reclassify_leaves_other_mentions_untouched():
    ml = _ml((0, 5, "PERSON"), (10, 20, "PERSON"))
    corr = [Correction(action=Action.RECLASSIFY, char_start=10, char_end=20, entity_type="ORG")]
    out = reconcile(ml, corr)
    by_span = {(m.char_start, m.char_end): m for m in out}
    assert by_span[(0, 5)].entity_type == "PERSON" and by_span[(0, 5)].source == "ml"
    assert by_span[(10, 20)].entity_type == "ORG" and by_span[(10, 20)].source == "human"


# --- reject ----------------------------------------------------------------


def test_reject_removes_the_spurious_mention():
    ml = _ml((0, 5, "PERSON"), (10, 20, "DATE"))
    corr = [Correction(action=Action.REJECT, char_start=10, char_end=20)]
    out = reconcile(ml, corr)
    assert [(m.char_start, m.char_end) for m in out] == [(0, 5)]


# --- add -------------------------------------------------------------------


def test_add_inserts_a_missed_entity():
    ml = _ml((0, 5, "PERSON"))
    corr = [
        Correction(action=Action.ADD, char_start=30, char_end=40, entity_type="ORG", surface="RAND")
    ]
    out = reconcile(ml, corr)
    by_span = {(m.char_start, m.char_end): m for m in out}
    assert by_span[(30, 40)].entity_type == "ORG"
    assert by_span[(30, 40)].surface == "RAND"
    assert by_span[(30, 40)].source == "human"


# --- adjust_span -----------------------------------------------------------


def test_adjust_span_moves_the_boundary_preserving_type():
    ml = _ml((10, 18, "PERSON", "Winston "))
    corr = [
        Correction(
            action=Action.ADJUST_SPAN,
            char_start=10,
            char_end=18,
            new_char_start=10,
            new_char_end=17,
        )
    ]
    out = reconcile(ml, corr)
    assert len(out) == 1
    assert (out[0].char_start, out[0].char_end) == (10, 17)
    assert out[0].entity_type == "PERSON"
    assert out[0].source == "human"


# --- exact surface rules ---------------------------------------------------


def test_reclassify_surface_changes_only_the_exact_surface_and_original_type():
    ml = _ml(
        (0, 5, "GPE", "Idaho"),
        (10, 15, "PERSON", "Idaho"),
        (20, 25, "GPE", "idaho"),
        (30, 35, "GPE", "Idaho"),
    )
    # A prior human decision of a different type is deliberately not swallowed.
    ml[1] = Mention(**{**vars(ml[1]), "source": "human"})
    correction = Correction(
        action=Action.RECLASSIFY_SURFACE,
        char_start=None,
        char_end=None,
        surface="Idaho",
        surface_original_type="GPE",
        entity_type="PERSON",
    )

    out = reconcile(ml, [correction])

    assert [(m.surface, m.entity_type, m.source) for m in out] == [
        ("Idaho", "PERSON", "human"),
        ("Idaho", "PERSON", "human"),
        ("idaho", "GPE", "ml"),
        ("Idaho", "PERSON", "human"),
    ]


def test_reject_surface_removes_only_the_exact_surface_and_original_type():
    ml = _ml(
        (0, 5, "GPE", "Idaho"),
        (10, 15, "PERSON", "Idaho"),
        (20, 25, "GPE", "idaho"),
        (30, 35, "GPE", "Idaho"),
    )
    correction = Correction(
        action=Action.REJECT_SURFACE,
        char_start=None,
        char_end=None,
        surface="Idaho",
        surface_original_type="GPE",
    )

    out = reconcile(ml, [correction])

    assert [(m.surface, m.entity_type) for m in out] == [
        ("Idaho", "PERSON"),
        ("idaho", "GPE"),
    ]


def test_surface_rules_are_idempotent_and_reapply_to_future_mentions():
    correction = Correction(
        action=Action.RECLASSIFY_SURFACE,
        char_start=None,
        char_end=None,
        surface="Idaho",
        surface_original_type="GPE",
        entity_type="PERSON",
    )
    first = reconcile(_ml((0, 5, "GPE", "Idaho")), [correction])
    assert reconcile(first, [correction]) == first
    future = reconcile(_ml((0, 5, "GPE", "Idaho"), (10, 15, "GPE", "Idaho")), [correction])
    assert [m.entity_type for m in future] == ["PERSON", "PERSON"]


def test_later_span_rule_overrides_an_earlier_surface_rule():
    corrections = [
        Correction(
            action=Action.RECLASSIFY_SURFACE,
            char_start=None,
            char_end=None,
            surface="Idaho",
            surface_original_type="GPE",
            entity_type="PERSON",
        ),
        Correction(action=Action.RECLASSIFY, char_start=0, char_end=5, entity_type="ORG"),
    ]
    assert reconcile(_ml((0, 5, "GPE", "Idaho")), corrections)[0].entity_type == "ORG"


def test_later_surface_rule_overrides_an_earlier_span_rule():
    corrections = [
        Correction(action=Action.RECLASSIFY, char_start=0, char_end=5, entity_type="PERSON"),
        Correction(
            action=Action.RECLASSIFY_SURFACE,
            char_start=None,
            char_end=None,
            surface="Idaho",
            surface_original_type="PERSON",
            entity_type="ORG",
        ),
    ]
    assert reconcile(_ml((0, 5, "GPE", "Idaho")), corrections)[0].entity_type == "ORG"


# --- the load-bearing invariant: survives re-annotation --------------------


def test_correction_survives_reannotation_when_ml_reproduces_the_span():
    # Deterministic re-annotation reproduces the same span; the correction
    # re-applies, so the corrected type is present again.
    corr = [Correction(action=Action.RECLASSIFY, char_start=10, char_end=20, entity_type="GPE")]
    first = reconcile(_ml((10, 20, "PERSON")), corr)
    second = reconcile(_ml((10, 20, "PERSON")), corr)  # a fresh identical ML run
    assert first == second
    assert second[0].entity_type == "GPE"


def test_reclassify_persists_even_if_ml_drops_the_span():
    # A different annotation run may no longer emit the span; the human's
    # decision is still honored (upsert semantics), so the entity is not lost.
    corr = [Correction(action=Action.RECLASSIFY, char_start=10, char_end=20, entity_type="GPE")]
    out = reconcile(_ml(), corr)  # ML produced nothing
    assert len(out) == 1
    assert (out[0].char_start, out[0].char_end, out[0].entity_type) == (10, 20, "GPE")
    assert out[0].source == "human"


def test_reconcile_is_idempotent():
    corr = [
        Correction(action=Action.RECLASSIFY, char_start=10, char_end=20, entity_type="GPE"),
        Correction(action=Action.ADD, char_start=30, char_end=40, entity_type="ORG", surface="R"),
        Correction(action=Action.REJECT, char_start=0, char_end=5),
    ]
    ml = _ml((0, 5, "PERSON"), (10, 20, "PERSON"))
    once = reconcile(ml, corr)
    twice = reconcile([Mention(**vars(m)) for m in once], corr)
    assert once == twice


def test_reconcile_output_is_deterministically_ordered_by_span():
    ml = _ml((30, 40, "ORG"), (0, 5, "PERSON"), (10, 20, "DATE"))
    out = reconcile(ml, [])
    assert [(m.char_start, m.char_end) for m in out] == [(0, 5), (10, 20), (30, 40)]


# --- edge cases ------------------------------------------------------------


def test_empty_corrections_returns_ml_unchanged_but_ordered():
    ml = _ml((10, 20, "PERSON"))
    out = reconcile(ml, [])
    assert out == ml


def test_reject_of_a_span_the_ml_no_longer_emits_is_a_noop():
    out = reconcile(
        _ml((0, 5, "PERSON")), [Correction(action=Action.REJECT, char_start=99, char_end=100)]
    )
    assert [(m.char_start, m.char_end) for m in out] == [(0, 5)]


def test_last_correction_for_a_span_wins():
    ml = _ml((10, 20, "PERSON"))
    corr = [
        Correction(action=Action.RECLASSIFY, char_start=10, char_end=20, entity_type="ORG"),
        Correction(action=Action.RECLASSIFY, char_start=10, char_end=20, entity_type="GPE"),
    ]
    out = reconcile(ml, corr)
    assert out[0].entity_type == "GPE"


# --- validation ------------------------------------------------------------


def test_reclassify_requires_an_entity_type():
    with pytest.raises(ValueError, match="entity_type"):
        Correction(action=Action.RECLASSIFY, char_start=10, char_end=20).validate()


def test_add_requires_an_entity_type():
    with pytest.raises(ValueError, match="entity_type"):
        Correction(action=Action.ADD, char_start=10, char_end=20).validate()


def test_adjust_span_requires_a_new_span():
    with pytest.raises(ValueError, match="new span"):
        Correction(action=Action.ADJUST_SPAN, char_start=10, char_end=20).validate()


def test_span_must_be_non_negative_and_ordered():
    with pytest.raises(ValueError, match="span"):
        Correction(action=Action.REJECT, char_start=20, char_end=10).validate()


@pytest.mark.parametrize("action", [Action.RECLASSIFY_SURFACE, Action.REJECT_SURFACE])
def test_surface_action_requires_surface_and_original_type(action):
    with pytest.raises(ValueError, match="surface"):
        Correction(
            action=action, char_start=None, char_end=None, surface_original_type="GPE"
        ).validate()
    with pytest.raises(ValueError, match="original type"):
        Correction(action=action, char_start=None, char_end=None, surface="Idaho").validate()


@pytest.mark.parametrize("action", [Action.RECLASSIFY_SURFACE, Action.REJECT_SURFACE])
def test_surface_action_rejects_span_coordinates(action):
    with pytest.raises(ValueError, match="without a target span"):
        Correction(
            action=action,
            char_start=0,
            char_end=5,
            surface="Idaho",
            surface_original_type="GPE",
            entity_type="PERSON" if action is Action.RECLASSIFY_SURFACE else None,
        ).validate()


def test_reclassify_surface_requires_a_distinct_entity_type():
    with pytest.raises(ValueError, match="requires an entity_type"):
        Correction(
            action=Action.RECLASSIFY_SURFACE,
            char_start=None,
            char_end=None,
            surface="Idaho",
            surface_original_type="GPE",
        ).validate()
    with pytest.raises(ValueError, match="different entity_type"):
        Correction(
            action=Action.RECLASSIFY_SURFACE,
            char_start=None,
            char_end=None,
            surface="Idaho",
            surface_original_type="GPE",
            entity_type="GPE",
        ).validate()


def test_span_actions_still_require_both_coordinates():
    with pytest.raises(ValueError, match="target span"):
        Correction(action=Action.REJECT, char_start=None, char_end=None).validate()
    with pytest.raises(ValueError, match="target span"):
        Correction(action=Action.REJECT, char_start=0, char_end=None).validate()


@pytest.mark.parametrize("new_start,new_end", [(-1, 4), (5, 4)])
def test_adjust_span_rejects_an_invalid_new_span(new_start, new_end):
    with pytest.raises(ValueError, match="invalid new span"):
        Correction(
            action=Action.ADJUST_SPAN,
            char_start=0,
            char_end=5,
            new_char_start=new_start,
            new_char_end=new_end,
        ).validate()
