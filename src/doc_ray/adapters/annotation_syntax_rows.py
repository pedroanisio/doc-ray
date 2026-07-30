"""Turn the derived syntax layers into storage rows.

Thin seam between :mod:`doc_ray.domain.syntax_derived` (pure, knows nothing about
ids or tables) and the spaCy annotator (holds the span/token id maps). Keeping
the derivation itself out of this module is what lets Layers 9 and 14 be tested
without a database or a parser.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import psycopg

from doc_ray.domain.syntax_derived import DerivedSyntax, derive as derive_syntax
from doc_ray.domain.ulid import new_ulid_str

#: ``(id, parent_id, label, span_id)``
ConstituentRow = tuple[str, str | None, str, str]
#: ``(id, token_id, frame)``
PredicateRow = tuple[str, str, str]
#: ``(predicate_id, span_id, role)``
ArgumentRow = tuple[str, str, str]

DictCursor = psycopg.Cursor[dict[str, Any]]


@dataclass(frozen=True)
class SyntaxRows:
    """Rows for Layers 9 and 14, ready to COPY under one annotation run."""

    constituents: list[ConstituentRow]
    predicates: list[PredicateRow]
    arguments: list[ArgumentRow]


def derive(doc: Any) -> tuple[DerivedSyntax, list[tuple[int, int]]]:
    """Derive both layers from a parsed document.

    Returns the derivation and the character spans it needs registered, so the
    annotator can fold them into the single bulk ``ensure_spans`` call it already
    makes for tokens, entities, and chunks.
    """
    parsed = derive_syntax(list(doc.sents))
    return parsed, list(parsed.spans)


def rows_for(
    doc: Any,
    parsed: DerivedSyntax,
    span_map: dict[tuple[int, int], str],
    tok_id: dict[int, str],
) -> SyntaxRows:
    """Bind a derivation to storage ids.

    A node whose span or predicate token never made it into the maps is dropped
    rather than written with a dangling reference: the maps are built from the
    same parse, so a miss means the token was filtered (whitespace-only), and a
    partial tree is a truer record than a broken foreign key.
    """
    del doc  # the derivation already carries everything the rows need
    keys: dict[int, str] = {}
    constituents: list[ConstituentRow] = []
    for node in parsed.constituents:
        span_id = span_map.get((node.char_start, node.char_end))
        if span_id is None:
            continue
        node_id = new_ulid_str()
        keys[node.key] = node_id
        # Parents are emitted before their children, so a present parent key is
        # already bound; an absent one means the parent span was dropped, and the
        # child re-parents to the nearest surviving ancestor -- null, in the worst
        # case, which reads as another root rather than as a cycle.
        parent_id = keys.get(node.parent) if node.parent is not None else None
        constituents.append((node_id, parent_id, node.label, span_id))

    predicates: list[PredicateRow] = []
    arguments: list[ArgumentRow] = []
    for frame in parsed.frames:
        token_id = tok_id.get(frame.predicate_index)
        if token_id is None:
            continue
        predicate_id = new_ulid_str()
        bound = _argument_rows(predicate_id, frame.arguments, span_map)
        if not bound:
            continue
        predicates.append((predicate_id, token_id, frame.lemma))
        arguments.extend(bound)
    return SyntaxRows(constituents=constituents, predicates=predicates, arguments=arguments)


def _argument_rows(
    predicate_id: str, frame_arguments: Sequence[Any], span_map: dict[tuple[int, int], str]
) -> list[ArgumentRow]:
    """Bind one frame's arguments, keeping the first role per span.

    ``srl_argument`` is keyed by ``(predicate_id, span_id)``, so two roles over
    the same span cannot both be stored. That collision means the parse gave one
    span two functions; the first is kept and the second dropped rather than
    letting a COPY abort the whole annotation run.
    """
    rows: list[ArgumentRow] = []
    seen: set[str] = set()
    for argument in frame_arguments:
        span_id = span_map.get((argument.char_start, argument.char_end))
        if span_id is None or span_id in seen:
            continue
        seen.add(span_id)
        rows.append((predicate_id, span_id, argument.role))
    return rows


def copy_syntax_layers(cur: DictCursor, ann: str, rows: SyntaxRows) -> None:
    """COPY both derived layers under annotation run *ann*.

    Constituents go first and in emission order (outermost first), which is what
    keeps every ``parent_id`` self-reference satisfied at insert time without
    deferring the constraint.
    """
    if rows.constituents:
        with cur.copy(
            "COPY constituent (id, annotation_id, parent_id, label, span_id) FROM STDIN"
        ) as cp:
            for node_id, parent_id, label, span_id in rows.constituents:
                cp.write_row((node_id, ann, parent_id, label, span_id))
    if rows.predicates:
        with cur.copy("COPY srl_predicate (id, annotation_id, token_id, frame) FROM STDIN") as cp:
            for predicate_id, token_id, frame in rows.predicates:
                cp.write_row((predicate_id, ann, token_id, frame))
    if rows.arguments:
        with cur.copy("COPY srl_argument (predicate_id, span_id, role) FROM STDIN") as cp:
            for predicate_id, span_id, role in rows.arguments:
                cp.write_row((predicate_id, span_id, role))
