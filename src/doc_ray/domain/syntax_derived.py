"""Layers 9 (constituency) and 14 (SRL), derived from the dependency parse.

WHY A DERIVATION AND NOT A PARSER. The capability matrix listed both layers as
schema-only, on the grounds that they "require a dedicated parser/model rather than
a safe deterministic fallback". That is correct about *gold* annotation — a Penn
Treebank bracketing and a PropBank roleset are human-annotated targets, and a neural
parser (benepar, AllenNLP SRL) approximates them with model output that would owe a
verification queue under PALS's Law. It is not correct about the whole layer: a
useful, checkable subset of both is recoverable from the parse the annotator already
produced, by construction and with no model. This module is that subset.

DERIVATION FROM FIRST PRINCIPLES.

*Constituency.* A dependency tree already encodes phrase structure implicitly: the
subtree rooted at a token is exactly the set of words that token governs, which is
what a phrase *is*. So for each token, its projected phrase spans its subtree, and
the phrase's label is a function of the token's UPOS (a nominal heads an NP, a verb
a VP, and so on). Two adjustments make the output match conventional bracketing:

1. A token whose only role is functional (determiner, case marker, punctuation,
   auxiliary, copula, complementizer, coordinator) is a preterminal, not a phrase.
   Projecting a unary node over each would add rows carrying no information.
2. A nominal governing an ADP ``case`` marker is the object of a preposition, so its
   projection is a PP, and the nominal alone remains an NP *inside* that PP. This is
   the nesting that Layer 8 chunking cannot express and Layer 9 exists to record.

The sentence root projects S over the whole sentence, plus a VP over the predicate —
the root's subtree minus its subject and sentence-final punctuation — which yields
the conventional ``S -> NP VP`` shape rather than a unary ``S -> VP`` chain.

*Semantic roles.* PropBank's proto-roles are recoverable from UD relations because
both encode the same argument structure: ``nsubj`` of an active verb is the
proto-agent (ARG0), its ``obj`` the proto-patient (ARG1), and passivisation swaps
which surface position carries which — ``nsubj:pass`` is the ARG1 and ``obl:agent``
the ARG0. Modifiers (``ARGM-*``) come from adjunct relations, with the ``case``
marker's lemma distinguishing locative from temporal.

WHAT THIS DELIBERATELY DOES NOT DO. It assigns no PropBank roleset number: the
``.01`` in ``found.01`` encodes a sense distinction requiring the PropBank frame
files and word-sense disambiguation (Layer 15, still schema-only), so the frame is
the bare lemma. Emitting ``.01`` would assert a sense that was never determined.
Non-projective subtrees are recorded by their covering span, since a discontinuous
constituent cannot be expressed as one ``(char_start, char_end)`` pair; the covering
span keeps the tree well-formed and is a superset, never a wrong subset.
"""

from __future__ import annotations

from dataclasses import dataclass

#: UPOS -> phrase label for a projecting head.
_PHRASE_LABEL: dict[str, str] = {
    "VERB": "VP",
    "AUX": "VP",
    "NOUN": "NP",
    "PROPN": "NP",
    "PRON": "NP",
    "NUM": "NP",
    "ADJ": "ADJP",
    "ADV": "ADVP",
    "SCONJ": "SBAR",
}

#: Relations that make a token a preterminal rather than a phrase head (adjustment 1).
_FUNCTIONAL_DEPRELS = frozenset(
    {
        "det",
        "case",
        "punct",
        "aux",
        "aux:pass",
        "cop",
        "mark",
        "cc",
        "dep",
        "expl",
        "discourse",
        "fixed",
        "flat",
        "goeswith",
        "compound",
        "nummod",
        "amod",
        "poss",
        "predet",
        "prt",
    }
)

#: A clausal dependent projects SBAR regardless of its own UPOS.
_CLAUSAL_DEPRELS = frozenset({"ccomp", "xcomp", "advcl", "acl", "acl:relcl", "csubj"})

#: Subjects and sentence punctuation sit outside the VP (see the S -> NP VP note).
_OUTSIDE_VP = frozenset({"nsubj", "nsubj:pass", "nsubjpass", "csubj", "expl", "punct"})

#: UD relation -> PropBank-style role for an active predicate's dependents.
_ROLE_BY_DEPREL: dict[str, str] = {
    "nsubj": "ARG0",
    "csubj": "ARG0",
    "agent": "ARG0",
    "obl:agent": "ARG0",
    "nsubj:pass": "ARG1",
    "nsubjpass": "ARG1",
    "obj": "ARG1",
    "dobj": "ARG1",
    "ccomp": "ARG1",
    "xcomp": "ARG1",
    "attr": "ARG1",
    "iobj": "ARG2",
    "dative": "ARG2",
    "advcl": "ARGM-ADV",
}

#: Case-marker lemmas that make an oblique locative or temporal. Kept small and
#: explicit: a marker outside these sets yields the unspecified ``ARGM`` rather than
#: a guess, because "at" alone does not tell you whether a place or a time follows.
_LOCATIVE_CASES = frozenset({"in", "on", "at", "into", "onto", "near", "inside", "outside", "from"})
_TEMPORAL_CASES = frozenset({"after", "before", "during", "since", "until", "till"})
_NEGATIONS = frozenset({"not", "never", "n't", "no"})


@dataclass(frozen=True)
class ParsedToken:
    """One dependency-parsed token: the whole input contract for both derivations.

    Attributes:
        index: 0-based position within its sentence.
        head_index: Index of the syntactic head; equal to ``index`` for the root.
        deprel: Universal Dependencies relation to the head.
        upos: Universal POS tag.
        lemma: Dictionary form, used as the SRL frame name.
        char_start: Start offset of the token in the document text.
        char_end: End offset (exclusive) of the token in the document text.
    """

    index: int
    head_index: int
    deprel: str
    upos: str
    lemma: str
    char_start: int
    char_end: int


@dataclass(frozen=True)
class ConstituentNode:
    """One phrase-structure node. ``key`` is stable within its sentence."""

    key: str
    parent_key: str | None
    label: str
    char_start: int
    char_end: int


@dataclass(frozen=True)
class SrlArgument:
    """One labelled argument span of a predicate."""

    role: str
    char_start: int
    char_end: int
    head_index: int


@dataclass(frozen=True)
class SrlFrame:
    """One predicate and its arguments."""

    predicate_index: int
    frame: str
    char_start: int
    char_end: int
    arguments: tuple[SrlArgument, ...]


def _children(tokens: list[ParsedToken]) -> dict[int, list[int]]:
    kids: dict[int, list[int]] = {t.index: [] for t in tokens}
    for token in tokens:
        if token.head_index != token.index and token.head_index in kids:
            kids[token.head_index].append(token.index)
    return kids


def _subtree(index: int, kids: dict[int, list[int]]) -> list[int]:
    """Indices of the subtree rooted at *index*, iteratively (no recursion limit)."""
    out: list[int] = []
    stack = [index]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if current in seen:  # a malformed cycle must not hang the derivation
            continue
        seen.add(current)
        out.append(current)
        stack.extend(kids.get(current, ()))
    return out


def _span(indices: list[int], by_index: dict[int, ParsedToken]) -> tuple[int, int]:
    starts = [by_index[i].char_start for i in indices]
    ends = [by_index[i].char_end for i in indices]
    return min(starts), max(ends)


def _root(tokens: list[ParsedToken]) -> ParsedToken:
    for token in tokens:
        if token.head_index == token.index or token.deprel.upper() == "ROOT":
            return token
    return tokens[0]


def _phrase_label(
    token: ParsedToken, kids: dict[int, list[int]], by_index: dict[int, ParsedToken]
) -> str | None:
    """The label *token* projects, or ``None`` when it is a preterminal."""
    if token.deprel in _CLAUSAL_DEPRELS:
        if _has_complementizer(token, kids, by_index):
            return "SBAR"
        return _PHRASE_LABEL.get(token.upos)
    if token.deprel in _FUNCTIONAL_DEPRELS:
        return None
    label = _PHRASE_LABEL.get(token.upos)
    if label == "NP" and _has_case_marker(token, kids, by_index):
        return "PP"  # adjustment 2: object of a preposition
    return label


def _has_case_marker(
    token: ParsedToken, kids: dict[int, list[int]], by_index: dict[int, ParsedToken]
) -> bool:
    return any(
        by_index[k].deprel.startswith("case") and by_index[k].upos == "ADP"
        for k in kids.get(token.index, ())
    )


def _has_complementizer(
    token: ParsedToken, kids: dict[int, list[int]], by_index: dict[int, ParsedToken]
) -> bool:
    return any(by_index[k].deprel == "mark" for k in kids.get(token.index, ()))


def _case_lemma(
    token: ParsedToken, kids: dict[int, list[int]], by_index: dict[int, ParsedToken]
) -> str | None:
    for k in kids.get(token.index, ()):
        if by_index[k].deprel.startswith("case"):
            return by_index[k].lemma.lower()
    return None


def derive_constituents(
    tokens: list[ParsedToken], *, key_prefix: str = "c"
) -> tuple[ConstituentNode, ...]:
    """Project a phrase-structure tree from one sentence's dependency parse.

    Args:
        tokens: The sentence's parsed tokens, any order (``index`` is authoritative).
        key_prefix: Prefix for the generated node keys, so a caller annotating many
            sentences can keep keys unique across them.

    Returns:
        Nodes in document order, exactly one with ``parent_key is None`` (the S).
    """
    if not tokens:
        return ()
    ordered = sorted(tokens, key=lambda t: t.index)
    by_index = {t.index: t for t in ordered}
    kids = _children(ordered)
    root = _root(ordered)

    sentence_span = _span([t.index for t in ordered], by_index)
    s_key = f"{key_prefix}-s"
    nodes: dict[int | str, ConstituentNode] = {}
    # The S node is keyed separately from any token: the root token may also project
    # a VP, and one token cannot own two nodes.
    nodes[s_key] = ConstituentNode(s_key, None, "S", *sentence_span)

    # The predicate VP: the root's subtree minus subjects and sentence punctuation.
    vp_indices = [
        i
        for i in _subtree(root.index, kids)
        if not (by_index[i].head_index == root.index and by_index[i].deprel in _OUTSIDE_VP)
    ]
    vp_key: str | None = None
    if _PHRASE_LABEL.get(root.upos) == "VP" and len(vp_indices) < len(ordered):
        vp_key = f"{key_prefix}-vp"
        nodes[vp_key] = ConstituentNode(vp_key, s_key, "VP", *_span(vp_indices, by_index))

    projected: dict[int, str] = {}
    for token in ordered:
        if token.index == root.index:
            continue
        label = _phrase_label(token, kids, by_index)
        if label is None:
            continue
        key = f"{key_prefix}-{token.index}"
        projected[token.index] = key
        node_span = _span(_subtree(token.index, kids), by_index)
        nodes[key] = ConstituentNode(key, None, label, *node_span)

    # Parent = nearest ancestor that projected; else the VP when inside it, else S.
    inside_vp = set(vp_indices)
    resolved: list[ConstituentNode] = [nodes[s_key]]
    if vp_key is not None:
        resolved.append(nodes[vp_key])
    for index, key in projected.items():
        parent_key = _nearest_projected_ancestor(index, by_index, projected, root.index)
        if parent_key is None:
            parent_key = vp_key if (vp_key is not None and index in inside_vp) else s_key
        node = nodes[key]
        resolved.append(
            ConstituentNode(node.key, parent_key, node.label, node.char_start, node.char_end)
        )

    # Adjustment 2's inner NP: a PP's nominal head is still an NP within it.
    for index, key in projected.items():
        if nodes[key].label != "PP":
            continue
        inner_indices = [
            i
            for i in _subtree(index, kids)
            if not (by_index[i].head_index == index and by_index[i].deprel.startswith("case"))
        ]
        inner_key = f"{key_prefix}-{index}-np"
        resolved.append(ConstituentNode(inner_key, key, "NP", *_span(inner_indices, by_index)))

    return tuple(sorted(resolved, key=lambda n: (n.char_start, -n.char_end, n.label)))


def _nearest_projected_ancestor(
    index: int,
    by_index: dict[int, ParsedToken],
    projected: dict[int, str],
    root_index: int,
) -> str | None:
    current = by_index[index].head_index
    seen: set[int] = {index}
    while current in by_index and current not in seen:
        if current == root_index:
            return None
        if current in projected:
            return projected[current]
        seen.add(current)
        current = by_index[current].head_index
    return None


def _modifier_role(
    dependent: ParsedToken, kids: dict[int, list[int]], by_index: dict[int, ParsedToken]
) -> str:
    """Role for an adjunct: locative/temporal when its case marker says so."""
    if dependent.deprel == "advmod" or dependent.upos == "PART":
        if dependent.lemma.lower() in _NEGATIONS:
            return "ARGM-NEG"
        return "ARGM-MNR"
    case = _case_lemma(dependent, kids, by_index)
    if case in _LOCATIVE_CASES:
        return "ARGM-LOC"
    if case in _TEMPORAL_CASES:
        return "ARGM-TMP"
    return "ARGM"


def derive_srl_frames(tokens: list[ParsedToken]) -> tuple[SrlFrame, ...]:
    """Derive one predicate-argument frame per verbal predicate in the sentence.

    Args:
        tokens: The sentence's parsed tokens.

    Returns:
        Frames in predicate document order; a predicate with no arguments is still
        returned, because the predicate itself is a finding.
    """
    if not tokens:
        return ()
    ordered = sorted(tokens, key=lambda t: t.index)
    by_index = {t.index: t for t in ordered}
    kids = _children(ordered)

    frames: list[SrlFrame] = []
    for token in ordered:
        # Auxiliaries are not predicates: "has left" is one frame, not two.
        if token.upos != "VERB" or token.deprel in ("aux", "aux:pass", "cop"):
            continue
        arguments: list[SrlArgument] = []
        for child_index in sorted(kids.get(token.index, ())):
            child = by_index[child_index]
            if child.deprel in _FUNCTIONAL_DEPRELS and child.upos != "PART":
                continue
            role = _ROLE_BY_DEPREL.get(child.deprel)
            if role is None:
                if child.deprel.startswith("obl") or child.deprel in ("advmod", "nmod"):
                    role = _modifier_role(child, kids, by_index)
                else:
                    continue
            start, end = _span(_subtree(child_index, kids), by_index)
            arguments.append(SrlArgument(role, start, end, child_index))
        frames.append(
            SrlFrame(
                predicate_index=token.index,
                frame=token.lemma.lower(),
                char_start=token.char_start,
                char_end=token.char_end,
                arguments=tuple(arguments),
            )
        )
    return tuple(frames)
