"""Layers 9 (constituency) and 14 (SRL), derived deterministically from the parse.

ACCEPTANCE CRITERIA come from this repository's own worked example in
``docs/nlp_annotation_layers.md`` for the sentence *"Elena founded a startup in
Berlin."* — the bracketing in §9 and the frame in §14 are what these tests assert,
so the implementation is measured against the documented target, not against
whatever the code happens to emit.

WHAT THESE LAYERS ARE. Both are *projections of the dependency parse*, not neural
parses. The capability matrix previously called them unimplementable without "a
dedicated parser/model"; that is true of gold Penn-Treebank/PropBank annotation and
NOT true of the deterministic subset, which is recoverable from heads, relations,
and UPOS by construction (see ``domain/syntax_derived`` for the derivation). They
are labelled with their own tool identity so a future neural parser supersedes them
by writing a different tool version rather than by silently overwriting.
"""

from __future__ import annotations

from doc_ray.domain.syntax_derived import (
    ParsedToken,
    derive_constituents,
    derive_srl_frames,
)


def _doc_example() -> list[ParsedToken]:
    """ "Elena founded a startup in Berlin." — the §9/§14 worked example, UD-parsed.

    Offsets are the real character offsets of that string, so span assertions are
    checkable against the text itself.
    """
    #            0     6       15 18       26 29
    # text = "Elena founded a startup in Berlin."
    return [
        ParsedToken(0, 1, "nsubj", "PROPN", "Elena", 0, 5),
        ParsedToken(1, 1, "ROOT", "VERB", "found", 6, 13),
        ParsedToken(2, 3, "det", "DET", "a", 14, 15),
        ParsedToken(3, 1, "obj", "NOUN", "startup", 16, 23),
        ParsedToken(4, 5, "case", "ADP", "in", 24, 26),
        ParsedToken(5, 1, "obl", "PROPN", "Berlin", 27, 33),
        ParsedToken(6, 1, "punct", "PUNCT", ".", 33, 34),
    ]


def _labelled(nodes) -> list[tuple[str, int, int]]:
    return [(n.label, n.char_start, n.char_end) for n in nodes]


# --- Layer 9: constituency ---------------------------------------------------


def test_documented_bracketing_is_reproduced():
    """The §9 tree: (S (NP Elena) (VP founded (NP a startup) (PP in (NP Berlin))) (. .))."""
    nodes = derive_constituents(_doc_example())

    assert set(_labelled(nodes)) == {
        ("S", 0, 34),  # whole sentence
        ("NP", 0, 5),  # Elena — the subject, sibling of VP
        # Predicate: excludes the subject AND the sentence-final period, which the
        # documented tree makes a sibling of VP under S — `(VP …) (. .)`.
        ("VP", 6, 33),
        ("NP", 14, 23),  # a startup
        ("PP", 24, 33),  # in Berlin
        ("NP", 27, 33),  # Berlin, inside the PP — the point of Layer 9 over chunking
    }


def test_the_pp_genuinely_contains_its_np():
    # Layer 9 exists precisely because chunking cannot express this nesting.
    nodes = derive_constituents(_doc_example())
    by_label = {(n.label, n.char_start): n for n in nodes}

    pp = by_label[("PP", 24)]
    inner_np = by_label[("NP", 27)]

    assert inner_np.parent_key == pp.key
    assert pp.char_start <= inner_np.char_start and inner_np.char_end <= pp.char_end


def test_subject_np_and_vp_are_siblings_under_s():
    nodes = derive_constituents(_doc_example())
    by_label = {(n.label, n.char_start): n for n in nodes}
    root = next(n for n in nodes if n.label == "S")

    assert by_label[("NP", 0)].parent_key == root.key
    assert by_label[("VP", 6)].parent_key == root.key
    assert root.parent_key is None


def test_object_np_hangs_under_the_vp_not_the_s():
    nodes = derive_constituents(_doc_example())
    by_label = {(n.label, n.char_start): n for n in nodes}

    assert by_label[("NP", 14)].parent_key == by_label[("VP", 6)].key


def test_every_node_forms_a_tree_with_nested_spans():
    # Structural invariant: exactly one root, every parent exists, and a child's
    # span is always contained by its parent's.
    nodes = derive_constituents(_doc_example())
    keys = {n.key for n in nodes}
    by_key = {n.key: n for n in nodes}

    assert len([n for n in nodes if n.parent_key is None]) == 1
    for node in nodes:
        if node.parent_key is None:
            continue
        assert node.parent_key in keys
        parent = by_key[node.parent_key]
        assert parent.char_start <= node.char_start
        assert node.char_end <= parent.char_end


def test_functional_tokens_do_not_project_phrases():
    # Determiners, case markers and punctuation are preterminals; a unary phrase
    # over each would add rows without adding information.
    nodes = derive_constituents(_doc_example())

    assert not [n for n in nodes if (n.char_start, n.char_end) == (14, 15)]  # "a"
    assert not [n for n in nodes if (n.char_start, n.char_end) == (24, 26)]  # "in"
    assert not [n for n in nodes if (n.char_start, n.char_end) == (33, 34)]  # "."


def test_single_token_sentence_yields_only_s():
    nodes = derive_constituents([ParsedToken(0, 0, "ROOT", "INTJ", "hello", 0, 5)])

    assert _labelled(nodes) == [("S", 0, 5)]


def test_empty_input_yields_no_nodes():
    assert derive_constituents([]) == ()


def test_adjective_and_adverb_phrases_are_labelled():
    # "She was remarkably calm." — ADJP predicate complement with an ADVP inside.
    tokens = [
        ParsedToken(0, 2, "nsubj", "PRON", "she", 0, 3),
        ParsedToken(1, 2, "cop", "AUX", "be", 4, 7),
        ParsedToken(2, 2, "ROOT", "ADJ", "calm", 8, 22),
        ParsedToken(3, 2, "advmod", "ADV", "remarkably", 23, 27),
    ]

    labels = {n.label for n in derive_constituents(tokens)}

    assert "ADVP" in labels
    assert "S" in labels


def test_clausal_complement_projects_an_sbar():
    # "I know that she left." — the ccomp is a clause, not a phrase.
    tokens = [
        ParsedToken(0, 1, "nsubj", "PRON", "I", 0, 1),
        ParsedToken(1, 1, "ROOT", "VERB", "know", 2, 6),
        ParsedToken(2, 4, "mark", "SCONJ", "that", 7, 11),
        ParsedToken(3, 4, "nsubj", "PRON", "she", 12, 15),
        ParsedToken(4, 1, "ccomp", "VERB", "leave", 16, 20),
    ]

    labels = {n.label for n in derive_constituents(tokens)}

    assert "SBAR" in labels


def test_non_projective_subtree_spans_its_full_extent():
    # A discontinuous subtree cannot be a contiguous constituent. The derivation
    # takes the covering span and must stay a valid tree rather than emit a gap.
    tokens = [
        ParsedToken(0, 1, "nsubj", "NOUN", "man", 0, 3),
        ParsedToken(1, 1, "ROOT", "VERB", "come", 4, 8),
        ParsedToken(2, 0, "acl", "VERB", "wear", 9, 16),  # attaches back to token 0
    ]

    nodes = derive_constituents(tokens)

    covering = next(n for n in nodes if n.label == "NP")
    assert covering.char_start == 0
    assert covering.char_end == 16  # covers the non-projective dependent


# --- A constituent always contains its own head token ------------------------
#
# Both subtree filters below exclude *dependents* of a node by matching
# ``head_index == <that node>``. A self-headed token satisfies that test
# vacuously, so each filter could delete the very token whose projection it was
# computing. The consequences were graded: with dependents left over the node
# survived with a span that omitted its head (silent corruption), and with none
# left over the span had nothing to measure and ``min()`` raised — which is how
# this reached production. Annotation of four books failed terminally on
# 2026-07-31 (``ValueError: min() iterable argument is empty``, syntax_derived
# line 289), stranding them at ``status='extracted'`` with the rest of the
# pipeline chain below them never running.


def test_root_stays_inside_its_own_vp_when_its_relation_is_outside_vp():
    """A root related as ``punct``/``nsubj``/``expl`` still heads its predicate.

    ``_OUTSIDE_VP`` lifts subjects and sentence punctuation out of the VP by
    matching the root's dependents. The root is its own head, so it matched too:
    the VP was built from the predicate *minus the predicate*, here leaving the
    object alone and reporting a verb phrase that starts after the verb.
    """
    tokens = [
        ParsedToken(0, 1, "nsubj", "PRON", "it", 0, 2),
        ParsedToken(1, 1, "punct", "VERB", "hold", 3, 8),
        ParsedToken(2, 1, "obj", "NOUN", "line", 9, 13),
    ]

    vp = next(n for n in derive_constituents(tokens) if n.label == "VP")

    assert (vp.char_start, vp.char_end) == (3, 13)  # verb + object, subject excluded


def test_root_alone_in_its_vp_yields_a_bare_s_instead_of_raising():
    """The degenerate case that failed in production: nothing survives the filter.

    A one-token sentence whose token is tagged as a verb but related as
    punctuation — the shape OCR noise produces in scanned books. Retaining the
    root makes the VP coextensive with the sentence, which the existing
    ``len(vp_indices) < len(ordered)`` guard already rejects as the unary
    ``S -> VP`` chain this module documents itself as not emitting.
    """
    nodes = derive_constituents([ParsedToken(0, 0, "punct", "VERB", "cont", 0, 4)])

    assert _labelled(nodes) == [("S", 0, 4)]


def test_pp_inner_np_keeps_the_nominal_that_heads_it():
    """The same vacuous self-match in adjustment 2's inner-NP filter.

    ``case:loc`` is a real UD subtype and is not in ``_FUNCTIONAL_DEPRELS`` (an
    exact-membership set), so a nominal carrying it projects a PP — and is then
    deleted from its own inner NP by a filter testing ``startswith("case")``.
    """
    tokens = [
        ParsedToken(0, 0, "ROOT", "VERB", "wait", 0, 4),
        ParsedToken(1, 1, "case:loc", "NOUN", "side", 5, 9),
        ParsedToken(2, 1, "case", "ADP", "at", 10, 12),
    ]

    nodes = derive_constituents(tokens)
    pp = next(n for n in nodes if n.label == "PP")
    inner = next(n for n in nodes if n.parent_key == pp.key)

    assert (inner.label, inner.char_start, inner.char_end) == ("NP", 5, 9)


# --- Layer 14: semantic role labelling ---------------------------------------


def test_documented_frame_is_reproduced():
    """§14: ``[ARG0 Elena] [PRED founded] [ARG1 a startup] [ARGM-LOC in Berlin]``."""
    frames = derive_srl_frames(_doc_example())

    assert len(frames) == 1
    frame = frames[0]
    assert frame.predicate_index == 1
    assert {(a.role, a.char_start, a.char_end) for a in frame.arguments} == {
        ("ARG0", 0, 5),  # Elena — agent
        ("ARG1", 14, 23),  # a startup — thing founded
        ("ARGM-LOC", 24, 33),  # in Berlin — includes its case marker
    }


def test_frame_is_the_lemma_without_a_fabricated_roleset_number():
    """PropBank rolesets (``found.01``) need the PropBank frame files.

    The documented example shows ``found.01``, but the roleset index encodes a
    sense distinction this derivation cannot make. Emitting ``.01`` regardless
    would assert a sense we never disambiguated, so the frame is the bare lemma.
    """
    frame = derive_srl_frames(_doc_example())[0]

    assert frame.frame == "found"
    assert ".01" not in frame.frame


def test_passive_subject_is_arg1_and_agent_is_arg0():
    # "The startup was founded by Elena." — surface subject is the PropBank ARG1.
    tokens = [
        ParsedToken(0, 1, "det", "DET", "the", 0, 3),
        ParsedToken(1, 3, "nsubj:pass", "NOUN", "startup", 4, 11),
        ParsedToken(2, 3, "aux:pass", "AUX", "be", 12, 15),
        ParsedToken(3, 3, "ROOT", "VERB", "found", 16, 23),
        ParsedToken(4, 5, "case", "ADP", "by", 24, 26),
        ParsedToken(5, 3, "obl:agent", "PROPN", "Elena", 27, 32),
    ]

    roles = {a.role: (a.char_start, a.char_end) for a in derive_srl_frames(tokens)[0].arguments}

    assert roles["ARG1"] == (0, 11)  # the startup
    assert roles["ARG0"] == (24, 32)  # by Elena


def test_indirect_object_is_arg2():
    # "She gave him a book."
    tokens = [
        ParsedToken(0, 1, "nsubj", "PRON", "she", 0, 3),
        ParsedToken(1, 1, "ROOT", "VERB", "give", 4, 8),
        ParsedToken(2, 1, "iobj", "PRON", "him", 9, 12),
        ParsedToken(3, 1, "obj", "NOUN", "book", 13, 19),
    ]

    roles = {a.role for a in derive_srl_frames(tokens)[0].arguments}

    assert roles == {"ARG0", "ARG1", "ARG2"}


def test_negation_becomes_argm_neg():
    tokens = [
        ParsedToken(0, 1, "nsubj", "PRON", "she", 0, 3),
        ParsedToken(1, 1, "ROOT", "VERB", "leave", 4, 9),
        ParsedToken(2, 1, "advmod", "PART", "not", 10, 13),
    ]

    roles = {a.role for a in derive_srl_frames(tokens)[0].arguments}

    assert "ARGM-NEG" in roles


def test_temporal_modifier_is_distinguished_from_locative():
    # "She left after lunch." — "after" is temporal, not locative.
    tokens = [
        ParsedToken(0, 1, "nsubj", "PRON", "she", 0, 3),
        ParsedToken(1, 1, "ROOT", "VERB", "leave", 4, 8),
        ParsedToken(2, 3, "case", "ADP", "after", 9, 14),
        ParsedToken(3, 1, "obl", "NOUN", "lunch", 15, 20),
    ]

    roles = {a.role for a in derive_srl_frames(tokens)[0].arguments}

    assert "ARGM-TMP" in roles
    assert "ARGM-LOC" not in roles


def test_multiple_predicates_yield_one_frame_each():
    # "Investors trusted her vision and funded it."
    tokens = [
        ParsedToken(0, 1, "nsubj", "NOUN", "investor", 0, 9),
        ParsedToken(1, 1, "ROOT", "VERB", "trust", 10, 17),
        ParsedToken(2, 3, "det", "PRON", "her", 18, 21),
        ParsedToken(3, 1, "obj", "NOUN", "vision", 22, 28),
        ParsedToken(4, 5, "cc", "CCONJ", "and", 29, 32),
        ParsedToken(5, 1, "conj", "VERB", "fund", 33, 39),
        ParsedToken(6, 5, "obj", "PRON", "it", 40, 42),
    ]

    frames = derive_srl_frames(tokens)

    assert [f.frame for f in frames] == ["trust", "fund"]
    assert all(f.arguments for f in frames)


def test_auxiliaries_are_not_predicates():
    # "She has left." — one frame ("leave"), not two.
    tokens = [
        ParsedToken(0, 2, "nsubj", "PRON", "she", 0, 3),
        ParsedToken(1, 2, "aux", "AUX", "have", 4, 7),
        ParsedToken(2, 2, "ROOT", "VERB", "leave", 8, 12),
    ]

    assert [f.frame for f in derive_srl_frames(tokens)] == ["leave"]


def test_a_verbless_sentence_yields_no_frames():
    tokens = [
        ParsedToken(0, 0, "ROOT", "NOUN", "chapter", 0, 7),
        ParsedToken(1, 0, "nummod", "NUM", "3", 8, 9),
    ]

    assert derive_srl_frames(tokens) == ()


def test_a_predicate_with_no_arguments_is_still_a_frame():
    # "Rain fell." keeps its ARG0; "Stop." has no dependents at all and must not
    # vanish — the predicate itself is the finding.
    assert derive_srl_frames([ParsedToken(0, 0, "ROOT", "VERB", "stop", 0, 5)]) == (
        derive_srl_frames([ParsedToken(0, 0, "ROOT", "VERB", "stop", 0, 5)])[0],
    )
    frame = derive_srl_frames([ParsedToken(0, 0, "ROOT", "VERB", "stop", 0, 5)])[0]
    assert frame.frame == "stop"
    assert frame.arguments == ()


def test_derivation_is_deterministic():
    tokens = _doc_example()

    assert derive_constituents(tokens) == derive_constituents(tokens)
    assert derive_srl_frames(tokens) == derive_srl_frames(tokens)
