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

from doc_ray.domain.search_terms import FOLD_SOURCE, FOLD_TARGET, match_plan


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
