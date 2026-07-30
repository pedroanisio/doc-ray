"""Spec tests for the derived syntax layers (9 · constituency, 14 · SRL).

These run without a database or a parser: the dependency analyses below are the
ones written out in ``docs/nlp_annotation_layers.md``, transcribed from its
CoNLL-U table. That makes the documented tree the executable spec — if the
derivation stops reproducing it, this fails rather than the doc quietly going
stale.

The layer's end-to-end behaviour (annotate writes it, GraphQL reads it) is
covered by ``test_api_layers.py``, which needs Postgres.
"""

from __future__ import annotations

from doc_ray.domain.syntax_derived import derive


class _Token:
    """A dependency-parse token — the attribute subset the derivation reads."""

    def __init__(
        self,
        i: int,
        text: str,
        idx: int,
        lemma: str,
        pos: str,
        dep: str,
        head: int,
        ent_type: str = "",
    ) -> None:
        self.i = i
        self.text = text
        self.idx = idx
        self.lemma_ = lemma
        self.pos_ = pos
        self.dep_ = dep
        self.ent_type_ = ent_type
        self._head = head
        self._sentence: list[_Token] = []

    @property
    def head(self) -> _Token:
        return self._sentence[self._head]


def _sentence(*tokens: _Token) -> list[_Token]:
    ordered = list(tokens)
    for token in ordered:
        token._sentence = ordered
    return ordered


def _elena_sentence(berlin_ent: str = "") -> list[_Token]:
    """``Elena founded a startup in Berlin.`` — the doc's worked example."""
    return _sentence(
        _Token(0, "Elena", 0, "Elena", "PROPN", "nsubj", 1, "PERSON"),
        _Token(1, "founded", 6, "found", "VERB", "root", 1),
        _Token(2, "a", 14, "a", "DET", "det", 3),
        _Token(3, "startup", 16, "startup", "NOUN", "obj", 1),
        _Token(4, "in", 24, "in", "ADP", "case", 5),
        _Token(5, "Berlin", 27, "Berlin", "PROPN", "obl", 1, berlin_ent),
        _Token(6, ".", 33, ".", "PUNCT", "punct", 1),
    )


TEXT = "Elena founded a startup in Berlin."


def _labelled(derived: object) -> list[tuple[str, str]]:
    return [
        (node.label, TEXT[node.char_start : node.char_end])
        for node in derived.constituents  # type: ignore[attr-defined]
    ]


def test_constituency_reproduces_the_documented_tree() -> None:
    """The derivation must rebuild the Penn bracketing in the layer doc."""
    derived = derive([_elena_sentence()])
    assert _labelled(derived) == [
        ("S", "Elena founded a startup in Berlin."),
        ("NP", "Elena"),
        ("VP", "founded a startup in Berlin"),
        ("NP", "a startup"),
        ("PP", "in Berlin"),
        ("NP", "Berlin"),
    ]


def test_prepositional_phrase_genuinely_contains_its_noun_phrase() -> None:
    """The nesting a chunker flattens is the reason this layer exists."""
    derived = derive([_elena_sentence()])
    by_label = {node.label: node for node in derived.constituents}
    assert by_label["NP"].char_start >= 0
    pp = next(n for n in derived.constituents if n.label == "PP")
    inner = next(n for n in derived.constituents if n.parent == pp.key)
    assert inner.label == "NP"
    assert TEXT[inner.char_start : inner.char_end] == "Berlin"


def test_function_words_do_not_become_one_word_phrases() -> None:
    """``a`` is a leaf of the NP above it, not an NP of its own."""
    derived = derive([_elena_sentence()])
    assert not [n for n in derived.constituents if TEXT[n.char_start : n.char_end] == "a"]


def test_exactly_one_root_for_a_well_formed_sentence() -> None:
    derived = derive([_elena_sentence()])
    assert len([n for n in derived.constituents if n.parent is None]) == 1


def test_frame_is_the_bare_lemma_not_a_propbank_roleset() -> None:
    """``found``, never ``found.01`` — the roleset asserts a sense nothing derived."""
    derived = derive([_elena_sentence()])
    assert [f.lemma for f in derived.frames] == ["found"]
    assert all(".01" not in f.lemma for f in derived.frames)


def test_core_roles_come_from_the_dependency_relations() -> None:
    derived = derive([_elena_sentence()])
    roles = {a.role: TEXT[a.char_start : a.char_end] for f in derived.frames for a in f.arguments}
    assert roles["ARG0"] == "Elena"
    assert roles["ARG1"] == "a startup"


def test_ambiguous_case_marker_is_not_guessed() -> None:
    """``in`` is locative or temporal by its object; unresolved stays ``ARGM``."""
    unresolved = derive([_elena_sentence()])
    assert any(a.role == "ARGM" for f in unresolved.frames for a in f.arguments)

    located = derive([_elena_sentence(berlin_ent="GPE")])
    assert any(a.role == "ARGM-LOC" for f in located.frames for a in f.arguments)

    dated = derive([_elena_sentence(berlin_ent="DATE")])
    assert any(a.role == "ARGM-TMP" for f in dated.frames for a in f.arguments)


def test_passive_voice_swaps_which_position_carries_arg1() -> None:
    """``The startup was founded by Elena.`` — surface subject is the thing acted on."""
    tokens = _sentence(
        _Token(0, "The", 0, "the", "DET", "det", 1),
        _Token(1, "startup", 4, "startup", "NOUN", "nsubj:pass", 3),
        _Token(2, "was", 12, "be", "AUX", "aux:pass", 3),
        _Token(3, "founded", 16, "found", "VERB", "root", 3),
        _Token(4, "by", 24, "by", "ADP", "case", 5),
        _Token(5, "Elena", 27, "Elena", "PROPN", "obl:agent", 3, "PERSON"),
        _Token(6, ".", 32, ".", "PUNCT", "punct", 3),
    )
    text = "The startup was founded by Elena."
    derived = derive([tokens])
    roles = {a.role: text[a.char_start : a.char_end] for f in derived.frames for a in f.arguments}
    assert roles["ARG1"] == "The startup", roles
    assert roles["ARG0"] == "by Elena", roles


def test_negation_is_recorded_as_its_own_modifier() -> None:
    tokens = _sentence(
        _Token(0, "Elena", 0, "Elena", "PROPN", "nsubj", 2),
        _Token(1, "never", 6, "never", "ADV", "advmod", 2),
        _Token(2, "founded", 12, "found", "VERB", "root", 2),
        _Token(3, "it", 20, "it", "PRON", "obj", 2),
    )
    derived = derive([tokens])
    roles = {a.role for f in derived.frames for a in f.arguments}
    assert "ARGM-NEG" in roles


def test_a_flat_parse_yields_sentence_nodes_only() -> None:
    """No dependents means no phrase the parse can justify — just the roots."""
    tokens = _sentence(
        _Token(0, "Elena", 0, "Elena", "PROPN", "ROOT", 0),
        _Token(1, "founded", 6, "found", "VERB", "ROOT", 1),
        _Token(2, "Berlin", 14, "Berlin", "PROPN", "ROOT", 2),
        _Token(3, ".", 20, ".", "PUNCT", "ROOT", 3),
    )
    derived = derive([tokens])
    assert {n.label for n in derived.constituents} == {"S"}
    assert len(derived.constituents) == 3
    # A predicate with no arguments states nothing the token does not already.
    assert derived.frames == ()


def test_registered_spans_cover_every_node_and_argument() -> None:
    """The annotator registers these in one bulk call, so none may be missing."""
    derived = derive([_elena_sentence(berlin_ent="GPE")])
    needed = {(n.char_start, n.char_end) for n in derived.constituents}
    needed |= {(a.char_start, a.char_end) for f in derived.frames for a in f.arguments}
    assert needed <= set(derived.spans)


def test_keys_are_unique_across_sentences() -> None:
    """Parent links are keys, so two sentences must not reuse one."""
    derived = derive([_elena_sentence(), _elena_sentence()])
    keys = [n.key for n in derived.constituents]
    assert len(keys) == len(set(keys))
    assert len([n for n in derived.constituents if n.parent is None]) == 2
