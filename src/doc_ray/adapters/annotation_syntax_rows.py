"""Build Layer 9 / Layer 14 rows from a spaCy ``Doc``.

Kept out of :mod:`doc_ray.adapters.annotation_spacy` for the D-015 net-LOC gate,
and because the shape is worth isolating: the *derivation* is pure domain logic
(:mod:`doc_ray.domain.syntax_derived`), and this module only converts its output
into the stand-off row tuples the schema wants — spans, ULIDs, and parent links.

Both layers are written under the same annotation run as the rest of the spaCy
layers, so ``DELETE FROM annotations`` cascades them away together and a
re-annotation replaces rather than accumulates.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, NamedTuple, Protocol

import psycopg

from doc_ray.domain.syntax_derived import (
    ParsedToken,
    derive_constituents,
    derive_srl_frames,
)
from doc_ray.domain.ulid import new_ulid_str


class SpacyTokenLike(Protocol):
    """The token surface these derivations read — nothing spaCy-specific beyond it.

    Declared structurally rather than importing ``spacy.tokens``: this module must
    stay importable in environments without the (heavy, worker-only) spaCy install,
    and stating the contract explicitly documents exactly which parser outputs the
    Layer 9/14 derivation depends on.
    """

    i: int
    idx: int
    text: str
    dep_: str
    pos_: str
    lemma_: str

    @property
    def head(self) -> SpacyTokenLike:
        """The token's syntactic head; itself when the token is the root."""
        ...


class SpacyDocLike(Protocol):
    """A parsed document exposing sentence spans of :class:`SpacyTokenLike`."""

    @property
    def sents(self) -> Iterable[Iterable[SpacyTokenLike]]:
        """The document's sentence spans, in reading order."""
        ...


class SyntaxRows(NamedTuple):
    """The Layer 9 + 14 rows for one document, threaded as one value.

    Bundled rather than passed as three parallel lists: they are written in one
    transaction, they are meaningless apart, and three extra parameters on the
    annotator's write path pushed that module past the D-015 net-LOC gate.
    """

    constituents: list[ConstituentRow]
    predicates: list[SrlPredicateRow]
    arguments: list[SrlArgumentRow]


#: Cursor with dict rows, mirroring ``annotation_spacy.DictCursor``.
DictCursor = psycopg.Cursor[dict[str, Any]]

#: (id, parent_id, label, span_id)
ConstituentRow = tuple[str, str | None, str, str]
#: (id, token_id, frame)
SrlPredicateRow = tuple[str, str, str]
#: (predicate_id, span_id, role)
SrlArgumentRow = tuple[str, str, str]


def parsed_sentences(doc: SpacyDocLike) -> list[list[ParsedToken]]:
    """Convert a spaCy ``Doc`` into one ``ParsedToken`` list per sentence.

    Whitespace-only tokens are dropped (they carry no syntax and are not
    registered as spans), and indices are made sentence-local so each sentence's
    derivation is independent of its position in the document.
    """
    out: list[list[ParsedToken]] = []
    for sent in doc.sents:
        keep = [t for t in sent if t.text.strip()]
        if not keep:
            continue
        local = {t.i: n for n, t in enumerate(keep)}
        tokens: list[ParsedToken] = []
        for token in keep:
            head_index = local.get(token.head.i, local[token.i])
            tokens.append(
                ParsedToken(
                    index=local[token.i],
                    head_index=head_index,
                    deprel="ROOT" if token.head.i == token.i else token.dep_.lower(),
                    upos=token.pos_,
                    lemma=token.lemma_ or token.text,
                    char_start=token.idx,
                    char_end=token.idx + len(token.text),
                )
            )
        out.append(tokens)
    return out


def required_spans(sentences: list[list[ParsedToken]]) -> list[tuple[int, int]]:
    """Every ``(char_start, char_end)`` the two layers will reference.

    Returned so the caller can register them in the SAME ``ensure_spans`` bulk
    call as tokens/entities/chunks: a second round-trip per document would cost a
    query per sentence on a 8,000-sentence book.
    """
    spans: list[tuple[int, int]] = []
    for index, tokens in enumerate(sentences):
        for node in derive_constituents(tokens, key_prefix=f"s{index}"):
            spans.append((node.char_start, node.char_end))
        for frame in derive_srl_frames(tokens):
            for argument in frame.arguments:
                spans.append((argument.char_start, argument.char_end))
    return spans


def build_syntax_rows(
    sentences: list[list[ParsedToken]],
    span_map: dict[tuple[int, int], str],
    token_id_by_offset: dict[tuple[int, int], str],
) -> SyntaxRows:
    """Convert derived structures into row tuples.

    Args:
        sentences: Parsed sentences from :func:`parsed_sentences`.
        span_map: ``(start, end) -> span_id``, already registered.
        token_id_by_offset: ``(start, end) -> token ULID``, for SRL predicates
            (``srl_predicate.token_id`` references ``tokens``, not ``spans``).

    Returns:
        The three row lists as a :class:`SyntaxRows`. A node or argument whose span
        was not registered is skipped rather than written with a dangling reference.
    """
    constituents: list[ConstituentRow] = []
    predicates: list[SrlPredicateRow] = []
    arguments: list[SrlArgumentRow] = []

    for index, tokens in enumerate(sentences):
        prefix = f"s{index}"
        ids: dict[str, str] = {}
        nodes = derive_constituents(tokens, key_prefix=prefix)
        for node in nodes:
            ids[node.key] = new_ulid_str()
        for node in nodes:
            span_id = span_map.get((node.char_start, node.char_end))
            if span_id is None:
                continue
            parent_id = ids.get(node.parent_key) if node.parent_key else None
            constituents.append((ids[node.key], parent_id, node.label, span_id))

        by_local_index = {t.index: t for t in tokens}
        for frame in derive_srl_frames(tokens):
            predicate_token = by_local_index.get(frame.predicate_index)
            if predicate_token is None:
                continue
            token_id = token_id_by_offset.get(
                (predicate_token.char_start, predicate_token.char_end)
            )
            if token_id is None:  # the predicate's token row was not written
                continue
            predicate_id = new_ulid_str()
            predicates.append((predicate_id, token_id, frame.frame))
            seen_spans: set[str] = set()
            for argument in frame.arguments:
                span_id = span_map.get((argument.char_start, argument.char_end))
                # srl_argument is PRIMARY KEY (predicate_id, span_id): two roles on
                # one span (possible when a subtree coincides) would abort the COPY.
                if span_id is None or span_id in seen_spans:
                    continue
                seen_spans.add(span_id)
                arguments.append((predicate_id, span_id, argument.role))
    return SyntaxRows(constituents, predicates, arguments)


def parents_before_children(rows: list[ConstituentRow]) -> list[ConstituentRow]:
    """Order constituent rows so every parent precedes its children.

    ``constituent.parent_id`` references ``constituent(id)`` and COPY performs no
    deferred constraint checking, so inserting a child before its parent fails the
    foreign key. Rows whose parent is absent from the batch are treated as roots so
    a malformed input degrades to a flat forest instead of dropping data.
    """
    by_id = {row[0]: row for row in rows}
    children: dict[str | None, list[ConstituentRow]] = {}
    for row in rows:
        parent = row[1] if row[1] in by_id else None
        children.setdefault(parent, []).append(row)
    ordered: list[ConstituentRow] = []
    stack = list(reversed(children.get(None, [])))
    while stack:
        row = stack.pop()
        ordered.append(row)
        stack.extend(reversed(children.get(row[0], [])))
    return ordered


def copy_syntax_layers(cur: DictCursor, ann: str, rows: SyntaxRows) -> None:
    """COPY the derived Layer-9 constituency and Layer-14 SRL rows.

    Constituents are written parents-first: ``constituent.parent_id`` is a
    self-reference, and COPY has no deferred constraint checking, so a child
    arriving before its parent would violate the foreign key.
    """
    constituents, srl_predicates, srl_arguments = rows
    if constituents:
        depth_ordered = parents_before_children(constituents)
        with cur.copy(
            "COPY constituent (id, annotation_id, parent_id, label, span_id) FROM STDIN"
        ) as cp:
            for cid, parent_id, label, span_id in depth_ordered:
                cp.write_row((cid, ann, parent_id, label, span_id))
    if srl_predicates:
        with cur.copy("COPY srl_predicate (id, annotation_id, token_id, frame) FROM STDIN") as cp:
            for pid, token_id, frame in srl_predicates:
                cp.write_row((pid, ann, token_id, frame))
    if srl_arguments:
        with cur.copy("COPY srl_argument (predicate_id, span_id, role) FROM STDIN") as cp:
            for pid, span_id, role in srl_arguments:
                cp.write_row((pid, span_id, role))


def derive(doc: SpacyDocLike) -> tuple[list[list[ParsedToken]], list[tuple[int, int]]]:
    """Parse *doc* into sentences and the spans Layers 9/14 will reference.

    One call so the annotator's hot path stays two lines: the spans must be known
    before ``ensure_spans`` runs, and the parse must be kept for the row build after.
    """
    parsed = parsed_sentences(doc)
    return parsed, required_spans(parsed)


def rows_for(
    doc: SpacyDocLike,
    sentences: list[list[ParsedToken]],
    span_map: dict[tuple[int, int], str],
    token_ids_by_index: dict[int, str],
) -> SyntaxRows:
    """Build the Layer 9/14 rows, mapping token indices to their stored ULIDs."""
    by_offset = {
        (t.idx, t.idx + len(t.text)): token_ids_by_index[t.i]
        for sent in doc.sents
        for t in sent
        if t.i in token_ids_by_index
    }
    return build_syntax_rows(sentences, span_map, by_offset)
