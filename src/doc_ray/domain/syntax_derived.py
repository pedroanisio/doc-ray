"""Derive constituency (Layer 9) and predicate-argument frames (Layer 14).

Both layers are *projections of the dependency parse* (Layer 10), not the output
of a constituency parser or a neural SRL model. That choice is the whole design:
the parse is already computed, so these layers cost one pass over tokens and add
no model, no download, and no GPU — but they can only assert what a dependency
tree actually determines.

Two limits follow, and are deliberately visible in the output rather than papered
over:

* Labels are restricted to :data:`PHRASE_LABELS` — the set a UD relation can
  justify. There is no gold Penn Treebank tag set here, so no ``NNP``/``VBD``
  preterminals are invented.
* A frame is named by its predicate LEMMA (``found``), never a PropBank roleset
  (``found.01``). The roleset index encodes a sense distinction that needs the
  PropBank frame files and Layer 15 (WSD, still schema-only); emitting it would
  assert a sense nothing determined.

The module is pure and framework-free: it reads only the token attributes any
dependency parse exposes (``i``, ``idx``, ``text``, ``lemma_``, ``pos_``,
``dep_``, ``head``) and never touches spaCy, psycopg, or a connection. Callers
hand it sentences; the adapter turns the result into rows.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol


class ParsedToken(Protocol):
    """The token surface this derivation depends on — a structural subset of spaCy's."""

    i: int
    idx: int
    text: str
    lemma_: str
    pos_: str
    dep_: str

    @property
    def head(self) -> Any: ...


#: Phrase labels the derivation is willing to assert.
PHRASE_LABELS = ("S", "NP", "VP", "PP", "ADJP", "ADVP", "SBAR")

#: UPOS -> phrase label for the head of a subtree.
_LABEL_FOR_UPOS = {
    "NOUN": "NP",
    "PROPN": "NP",
    "PRON": "NP",
    "NUM": "NP",
    "DET": "NP",
    "VERB": "VP",
    "AUX": "VP",
    "ADP": "PP",
    "ADJ": "ADJP",
    "ADV": "ADVP",
    "SCONJ": "SBAR",
}

#: Parts of speech that head a phrase on their own. Anything else needs
#: dependents before it is worth a node -- otherwise every determiner and
#: preposition becomes a spurious one-word phrase.
_CONTENT_UPOS = frozenset({"NOUN", "PROPN", "PRON", "NUM", "VERB", "ADJ", "ADV"})

#: Dependency relations naming a clausal subject.
_SUBJECT_DEPS = frozenset({"nsubj", "nsubjpass", "csubj"})
_PASSIVE_SUBJECT_DEPS = frozenset({"nsubj:pass", "nsubjpass", "csubj:pass"})
_OBJECT_DEPS = frozenset({"obj", "dobj"})
_INDIRECT_OBJECT_DEPS = frozenset({"iobj", "dative"})
_OBLIQUE_DEPS = frozenset({"obl", "nmod", "obl:agent", "obl:tmod"})

#: Prepositions whose role is not in genuine doubt. ``in``/``on``/``at`` are
#: absent on purpose: they are spatial or temporal depending on their object, and
#: are resolved by :func:`_ambiguous_case_role` rather than assumed.
_UNAMBIGUOUS_CASE_ROLES = {
    "into": "ARGM-LOC",
    "onto": "ARGM-LOC",
    "near": "ARGM-LOC",
    "under": "ARGM-LOC",
    "above": "ARGM-LOC",
    "below": "ARGM-LOC",
    "beside": "ARGM-LOC",
    "inside": "ARGM-LOC",
    "outside": "ARGM-LOC",
    "toward": "ARGM-LOC",
    "towards": "ARGM-LOC",
    "during": "ARGM-TMP",
    "before": "ARGM-TMP",
    "after": "ARGM-TMP",
    "since": "ARGM-TMP",
    "until": "ARGM-TMP",
    "till": "ARGM-TMP",
    "throughout": "ARGM-TMP",
    "via": "ARGM-MNR",
    "without": "ARGM-MNR",
}

#: Case markers that read as location or time depending on what they govern.
_AMBIGUOUS_CASE_MARKERS = frozenset({"in", "on", "at"})

#: Entity types that settle an ambiguous case marker toward time.
_TEMPORAL_ENT_TYPES = frozenset({"DATE", "TIME"})

_NEGATION_LEMMAS = frozenset({"not", "n't", "never", "no"})


@dataclass(frozen=True)
class DerivedConstituent:
    """One phrase-structure node, addressed by character offsets.

    ``key`` is unique within a derivation and ``parent`` refers to another node's
    ``key``; the adapter swaps both for storage ids. A discontinuous (non-projective)
    subtree is recorded by its covering span, because one offset pair cannot express
    a gap.
    """

    key: int
    parent: int | None
    label: str
    char_start: int
    char_end: int


@dataclass(frozen=True)
class DerivedArgument:
    """One labelled argument of a frame (``ARG0``, ``ARGM-LOC``, ...)."""

    role: str
    char_start: int
    char_end: int


@dataclass(frozen=True)
class DerivedFrame:
    """One predicate and the arguments its dependents license."""

    predicate_index: int
    lemma: str
    char_start: int
    char_end: int
    arguments: tuple[DerivedArgument, ...] = ()


@dataclass(frozen=True)
class DerivedSyntax:
    """Everything both derived layers produced for one document."""

    constituents: tuple[DerivedConstituent, ...] = ()
    frames: tuple[DerivedFrame, ...] = ()
    #: Every distinct character span the layers need registered, in emission order.
    spans: tuple[tuple[int, int], ...] = field(default=())


def _extent(tokens: Sequence[ParsedToken]) -> tuple[int, int]:
    """Return the character span covering *tokens*."""
    return min(t.idx for t in tokens), max(t.idx + len(t.text) for t in tokens)


def _children_of(tokens: Sequence[ParsedToken]) -> dict[int, list[ParsedToken]]:
    """Map each token index to its dependents.

    Built by scanning heads rather than reading ``token.children`` so the
    derivation works against any parse that exposes ``head``, not only a live
    spaCy ``Doc``.
    """
    children: dict[int, list[ParsedToken]] = {}
    for token in tokens:
        head = token.head
        if head is None or head.i == token.i:
            continue
        children.setdefault(head.i, []).append(token)
    return children


def _subtree(
    token: ParsedToken,
    children: dict[int, list[ParsedToken]],
    *,
    exclude: frozenset[int] = frozenset(),
) -> list[ParsedToken]:
    """Return *token* and its descendants, skipping any subtree rooted in *exclude*."""
    collected: list[ParsedToken] = []
    stack = [token]
    while stack:
        current = stack.pop()
        if current.i in exclude:
            continue
        collected.append(current)
        stack.extend(children.get(current.i, ()))
    return collected


def _is_punct(token: ParsedToken) -> bool:
    return token.pos_ == "PUNCT" or token.dep_ == "punct"


class _SentenceDerivation:
    """Derives both layers for one sentence, sharing the child map between them."""

    def __init__(self, tokens: Sequence[ParsedToken]) -> None:
        self.tokens = tokens
        self.children = _children_of(tokens)
        self.constituents: list[DerivedConstituent] = []
        self.frames: list[DerivedFrame] = []
        self._next_key = 0

    def _emit(self, parent: int | None, label: str, span: tuple[int, int]) -> int:
        key = self._next_key
        self._next_key += 1
        self.constituents.append(
            DerivedConstituent(
                key=key, parent=parent, label=label, char_start=span[0], char_end=span[1]
            )
        )
        return key

    # -- Layer 9 ------------------------------------------------------------

    def derive_constituents(self, key_offset: int) -> None:
        """Project a phrase tree, one ``S`` per root.

        A well-formed sentence has exactly one root, so this normally emits one
        ``S``. A fragment (or a stub parse where tokens head themselves) has
        several, and each becomes its own ``S`` rather than being forced under a
        sentence node the parse never justified.
        """
        self._next_key = key_offset
        roots = [t for t in self.tokens if not _is_punct(t) and t.head.i == t.i]
        for root in roots:
            # S is the sentence node, so it keeps the punctuation its subtree
            # carries; only the phrase nodes below it are trimmed.
            sentence_key = self._emit(None, "S", _extent(_subtree(root, self.children)))
            self._descend_root(root, sentence_key)

    def _descend_root(self, root: ParsedToken, parent_key: int) -> None:
        """Place the root's phrase under ``S``, lifting subjects to sibling position."""
        kids = [t for t in self.children.get(root.i, ()) if not _is_punct(t)]
        subjects = [t for t in kids if t.dep_ in _SUBJECT_DEPS | _PASSIVE_SUBJECT_DEPS]
        predicate_kids = [t for t in kids if t not in subjects]
        for subject in subjects:
            self._descend(subject, parent_key)
        label = _LABEL_FOR_UPOS.get(root.pos_)
        if label is None or not predicate_kids:
            for kid in predicate_kids:
                self._descend(kid, parent_key)
            return
        excluded = frozenset(t.i for t in subjects)
        covered = [t for t in _subtree(root, self.children, exclude=excluded) if not _is_punct(t)]
        phrase_key = self._emit(parent_key, label, _extent(covered or [root]))
        for kid in predicate_kids:
            self._descend(kid, phrase_key)

    def _descend(self, token: ParsedToken, parent_key: int) -> None:
        """Emit the phrase headed by *token*, then recurse into its dependents."""
        kids = [t for t in self.children.get(token.i, ()) if not _is_punct(t)]
        label = _LABEL_FOR_UPOS.get(token.pos_)
        if label is None or (not kids and token.pos_ not in _CONTENT_UPOS):
            # A bare function word is a leaf of the phrase above it, not a phrase
            # of its own: the determiner in `a startup` belongs to that NP rather
            # than heading one.
            for kid in kids:
                self._descend(kid, parent_key)
            return
        cases = [t for t in kids if t.dep_ == "case"]
        covered = [t for t in _subtree(token, self.children) if not _is_punct(t)]
        if cases and label == "NP":
            # `in Berlin` is a PP that contains the NP -- the nesting a chunker
            # flattens away, and the reason this layer exists at all.
            phrase_key = self._emit(parent_key, "PP", _extent(covered or [token]))
            excluded = frozenset(t.i for t in cases)
            inner = [
                t for t in _subtree(token, self.children, exclude=excluded) if not _is_punct(t)
            ]
            parent_key = self._emit(phrase_key, "NP", _extent(inner or [token]))
            kids = [t for t in kids if t not in cases]
        else:
            parent_key = self._emit(parent_key, label, _extent(covered or [token]))
        for kid in kids:
            self._descend(kid, parent_key)

    # -- Layer 14 -----------------------------------------------------------

    def derive_frames(self) -> None:
        """Emit one frame per verbal predicate that actually licenses an argument."""
        for token in self.tokens:
            if token.pos_ not in ("VERB", "AUX"):
                continue
            arguments = self._arguments_for(token)
            if not arguments:
                # A predicate with no arguments states nothing a reader could not
                # get from the token itself, so it is not recorded as a frame.
                continue
            self.frames.append(
                DerivedFrame(
                    predicate_index=token.i,
                    lemma=token.lemma_ or token.text.lower(),
                    char_start=token.idx,
                    char_end=token.idx + len(token.text),
                    arguments=arguments,
                )
            )

    def _arguments_for(self, predicate: ParsedToken) -> tuple[DerivedArgument, ...]:
        kids = [t for t in self.children.get(predicate.i, ()) if not _is_punct(t)]
        passive = any(t.dep_ in _PASSIVE_SUBJECT_DEPS for t in kids)
        arguments: list[DerivedArgument] = []
        for kid in kids:
            role = self._role_for(kid, passive=passive)
            if role is None:
                continue
            covered = [t for t in _subtree(kid, self.children) if not _is_punct(t)]
            start, end = _extent(covered or [kid])
            arguments.append(DerivedArgument(role=role, char_start=start, char_end=end))
        return tuple(arguments)

    def _role_for(self, token: ParsedToken, *, passive: bool) -> str | None:
        """Map one dependent onto a role, or ``None`` when it licenses no argument."""
        dep = token.dep_
        if dep in _PASSIVE_SUBJECT_DEPS:
            # The surface subject of a passive is the thing acted upon.
            return "ARG1"
        if dep in _SUBJECT_DEPS:
            return "ARG1" if passive else "ARG0"
        if dep in _OBJECT_DEPS:
            return "ARG1"
        if dep in _INDIRECT_OBJECT_DEPS:
            return "ARG2"
        if dep == "obl:agent":
            return "ARG0"
        if dep in _OBLIQUE_DEPS:
            return self._oblique_role(token)
        if dep == "neg":
            return "ARGM-NEG"
        if dep == "advmod":
            lemma = (token.lemma_ or token.text).lower()
            return "ARGM-NEG" if lemma in _NEGATION_LEMMAS else "ARGM-ADV"
        return None

    def _oblique_role(self, token: ParsedToken) -> str:
        """Label an oblique from its case marker, refusing to guess when ambiguous."""
        cases = [t for t in self.children.get(token.i, ()) if t.dep_ == "case"]
        if not cases:
            return "ARGM"
        marker = (cases[0].lemma_ or cases[0].text).lower()
        if marker == "by":
            return "ARG0"
        settled = _UNAMBIGUOUS_CASE_ROLES.get(marker)
        if settled:
            return settled
        if marker in _AMBIGUOUS_CASE_MARKERS:
            return _ambiguous_case_role(token)
        return "ARGM"


def _ambiguous_case_role(token: ParsedToken) -> str:
    """Resolve ``in``/``on``/``at`` from what they govern, not from a default.

    ``in Berlin`` is a location and ``in 1999`` is a time; the marker alone cannot
    tell them apart. The entity type settles it when the parse carries one, and
    when nothing settles it the unspecified ``ARGM`` is emitted rather than a
    coin-flip between LOC and TMP.
    """
    ent_type = getattr(token, "ent_type_", "") or ""
    if ent_type in _TEMPORAL_ENT_TYPES:
        return "ARGM-TMP"
    if ent_type:
        return "ARGM-LOC"
    if getattr(token, "like_num", False):
        return "ARGM-TMP"
    return "ARGM"


def derive(sentences: Iterable[Sequence[ParsedToken]]) -> DerivedSyntax:
    """Derive both layers for a document, given its sentences' tokens.

    Returns the nodes, the frames, and every character span the two layers need
    registered — collected here so the caller can register them in the one bulk
    call it already makes, rather than paying a round-trip per sentence.
    """
    constituents: list[DerivedConstituent] = []
    frames: list[DerivedFrame] = []
    spans: list[tuple[int, int]] = []
    for tokens in sentences:
        usable = [t for t in tokens if t.text.strip()]
        if not usable:
            continue
        derivation = _SentenceDerivation(usable)
        derivation.derive_constituents(len(constituents))
        derivation.derive_frames()
        constituents.extend(derivation.constituents)
        frames.extend(derivation.frames)
        spans.extend((node.char_start, node.char_end) for node in derivation.constituents)
        spans.extend(
            (argument.char_start, argument.char_end)
            for frame in derivation.frames
            for argument in frame.arguments
        )
    return DerivedSyntax(
        constituents=tuple(constituents), frames=tuple(frames), spans=tuple(spans)
    )
