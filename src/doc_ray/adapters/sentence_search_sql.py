"""Render a :class:`~doc_ray.domain.search_terms.MatchPlan` as a SQL predicate.

The seam between the pure matching rule (``domain.search_terms``) and the two
surfaces that search sentences — the GraphQL/MCP read adapter
(``persistence_read.search_sentences``) and the webapp's keyword route. Both used
to inline their own ``s.text ILIKE '%' || term || '%'``, which is how they drifted
into two subtly different answers to the same question. One builder, one
predicate, both surfaces.

The generated SQL is deliberately boring: an OR of escaped ``ILIKE`` patterns,
each of which the ``gin_trgm_ops`` index on ``sentences.text`` (migration 002)
serves exactly as it served the single pattern before. Only the rare
``fold_column`` plan gives that up, and only for terms too typographically
ambiguous to enumerate.
"""

from __future__ import annotations

from typing import Any

from doc_ray.domain.phrase_profile import phrase_words
from doc_ray.domain.search_terms import FOLD_SOURCE, FOLD_TARGET, match_plan

#: Scalar pattern slots on ``docray_readable_sentence_ids`` (migration 148).
#: Fixed arity, because only separate scalar ``ILIKE`` terms reach the trigram
#: index — ``ILIKE ALL (array)`` and an ``unnest`` form both fall back to a
#: sequential scan. Unused slots are padded with :data:`MATCH_ALL_PATTERN`.
MAX_INDEX_PATTERNS = 4
#: Matches everything and yields no trigram, so a pad costs nothing.
MATCH_ALL_PATTERN = "%"
#: pg_trgm cannot extract a trigram from fewer than three characters.
_MIN_TRIGRAM_CHARS = 3


def indexable_patterns(term: str) -> tuple[str, ...]:
    r"""Return up to :data:`MAX_INDEX_PATTERNS` patterns that can narrow via the index.

    Only plans that reduce to a *single* unfolded ``ILIKE`` pattern qualify. An
    OR of confusable spellings must not be reduced to one branch — that would
    drop true matches rather than merely widening the candidate set — and a
    ``translate()``-folded column cannot use the index at all. Such words are
    omitted, and a term with no usable word yields ``()``, which tells the caller
    to keep the existing predicate.

    Ordered longest-first for the same reason as
    :func:`phrase_candidate_clause`: without corpus frequency statistics on this
    path, word length is the stable selectivity proxy, and only the first four
    survive the arity limit.

    The decomposition mirrors the clause this replaces, and must. A single-word
    term keeps its punctuation, because :func:`match_plan` treats ``%``, ``_``
    and ``\\`` as *data* — reducing ``quxscan%`` to the profile word ``quxscan``
    would silently widen the search to sentences with no literal percent in
    them. Only a genuine multi-word phrase is split, exactly as
    :func:`phrase_candidate_clause` splits it.
    """
    words = phrase_words(term)
    sources = list(words) if len(words) > 1 else [term.strip()]

    ordered: list[tuple[int, str]] = []
    seen: set[str] = set()
    for index, word in enumerate(sources):
        if word and word not in seen:
            ordered.append((index, word))
            seen.add(word)
    ordered.sort(key=lambda item: (-len(item[1]), item[0]))

    patterns: list[str] = []
    for _index, word in ordered:
        if len(word) < _MIN_TRIGRAM_CHARS:
            continue
        plan = match_plan(word)
        if plan.fold_column or len(plan.patterns) != 1:
            continue
        patterns.append(plan.patterns[0])
        if len(patterns) == MAX_INDEX_PATTERNS:
            break
    return tuple(patterns)


def padded_index_patterns(term: str) -> tuple[str, ...] | None:
    """Patterns padded to the helper's fixed arity, or ``None`` if unusable."""
    patterns = indexable_patterns(term)
    if not patterns:
        return None
    pad = [MATCH_ALL_PATTERN] * (MAX_INDEX_PATTERNS - len(patterns))
    return tuple(patterns) + tuple(pad)


def match_clause(column: str, term: str, *, prefix: str = "q") -> tuple[str, dict[str, Any]]:
    r"""Return an SQL predicate matching *column* against the literal *term*.

    Args:
        column: The SQL expression to test (e.g. ``"s.text"``). Caller-supplied
            SQL text, never user input.
        term: The caller's literal search term. ``%``, ``_`` and ``\`` are data.
        prefix: Namespace for the generated parameter names, so a query can carry
            more than one clause without collision.

    Returns:
        ``(predicate_sql, params)`` — a parenthesised predicate safe to ``AND``
        into a larger ``WHERE``, and the named parameters it references.
    """
    plan = match_plan(term)
    params: dict[str, Any] = {}
    target = column
    if plan.fold_column:
        params[f"{prefix}_src"] = FOLD_SOURCE
        params[f"{prefix}_dst"] = FOLD_TARGET
        target = f"translate({column}, %({prefix}_src)s, %({prefix}_dst)s)"
    tests = []
    for index, pattern in enumerate(plan.patterns):
        name = f"{prefix}_pat{index}"
        params[name] = pattern
        # ESCAPE is the Postgres default backslash, named explicitly: the escapes
        # come from domain.search_terms and must not depend on a session setting.
        tests.append(f"{target} ILIKE %({name})s ESCAPE '\\'")
    return "(" + " OR ".join(tests) + ")", params


def phrase_candidate_clause(
    column: str, phrase: str, *, prefix: str = "phrase"
) -> tuple[str, dict[str, Any]]:
    """Return a parameterized AND-of-words superset for phrase verification.

    Candidate generation deliberately does not try to encode boundaries or
    adjacency in SQL. Every verified ``surface_word_v1`` occurrence contains
    each query word as a substring, so AND-ing the existing literal/confusable
    predicates cannot drop a true match. The domain verifier removes reordered,
    inflected, word-internal, and punctuation-separated false positives.

    PostgreSQL's trigram index needs at least three characters to be selective.
    Without corpus-wide frequency statistics on this request path, word length
    is the stable selectivity proxy: unique words of length >=3 lead, longest
    first, followed by shorter words. All words remain in the AND so the clause
    stays as selective as the query permits; all-short queries degrade honestly.
    """
    ordered: list[tuple[int, str]] = []
    seen: set[str] = set()
    for index, word in enumerate(phrase_words(phrase)):
        if word not in seen:
            ordered.append((index, word))
            seen.add(word)
    ordered.sort(key=lambda item: (len(item[1]) < 3, -len(item[1]), item[0]))
    if not ordered:
        return "(FALSE)", {}

    clauses: list[str] = []
    params: dict[str, Any] = {}
    for position, (_source_index, word) in enumerate(ordered):
        clause, clause_params = match_clause(column, word, prefix=f"{prefix}_w{position}")
        clauses.append(clause)
        params.update(clause_params)
    return "(" + " AND ".join(clauses) + ")", params
