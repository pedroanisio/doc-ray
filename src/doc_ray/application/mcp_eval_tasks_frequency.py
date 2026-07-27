"""The two term-frequency pre-flight eval tasks (D-015 split from the task set).

Both exercise the same tool from opposite sides: ``_TERMFREQ`` proves the
lexeme-lemma path stays reachable when the index is empty, ``_PHRASEFREQ`` proves
a multi-word term comes back measured rather than declined (#39).
"""

from __future__ import annotations

from doc_ray.application.mcp_eval import Assertion, EvalTask, Step
from doc_ray.application.mcp_eval_fixtures import (
    ABSENT_PHRASE,
    FLOOD_TERM,
    PHRASE_TERM,
    RARE_TERM,
)
from doc_ray.domain import mcp_eval_scoring as scoring
from doc_ray.domain.mcp_eval_scoring import TaskBudget
from doc_ray.entrypoints.mcp import structure


def _within(budget: TaskBudget) -> Assertion:
    """Budget assertion, mirroring the task set's helper."""
    return Assertion("within_budget", lambda calls: scoring.within_budget(calls, budget))


_TERMFREQ = EvalTask(
    id="coverage-term-frequency",
    kind="coverage",
    # The flood-risk pre-flight (Item 10). The fixture seeds sentences directly
    # rather than running the lexeme pipeline, so lexeme_stats is empty and every
    # term reads back unattested — which exercises the real GraphQL -> lexeme_stats
    # read path and the FR-6 unattested branch, and is deterministic (independent
    # of any ambient rollup state). The verdict discrimination itself (a flood term
    # scoring "high" from real counts) is proven in test_term_frequency_db.py.
    prompt="Before searching, check how common these candidate terms are.",
    grounded_in="reachability: term_frequency (Item 10 flood-risk pre-flight)",
    steps=(
        Step(
            structure.term_frequency,
            lambda ctx: {"terms": [FLOOD_TERM, RARE_TERM, "zzunattested"]},
        ),
    ),
    assertions=(
        Assertion(
            "term_frequency_ok",
            lambda calls: scoring.check_tool_ok(calls, "term_frequency"),
        ),
        _within(TaskBudget(max_calls=2, max_chars=15_000)),
    ),
    budget=TaskBudget(max_calls=2, max_chars=15_000),
)

_PHRASEFREQ = EvalTask(
    id="coverage-phrase-frequency",
    kind="coverage",
    # The measured half of the pre-flight (#39). Unlike _TERMFREQ, this does NOT
    # depend on the lexeme pipeline: the sentence scan reads sentences.text, which
    # the fixture seeds directly, so both phrases are answered from real data —
    # one present in the filler sentences, one nowhere in the corpus.
    prompt="Before searching, check how common this multi-word phrase is.",
    grounded_in="pre-flight: multi-word terms are measured, not declined (#39)",
    steps=(
        Step(
            structure.term_frequency,
            lambda ctx: {"terms": [PHRASE_TERM, ABSENT_PHRASE]},
        ),
    ),
    assertions=(
        Assertion(
            "term_frequency_ok",
            lambda calls: scoring.check_tool_ok(calls, "term_frequency"),
        ),
        Assertion(
            "phrase_measured",
            lambda calls: scoring.check_phrase_measured(calls, PHRASE_TERM, ABSENT_PHRASE),
        ),
        _within(TaskBudget(max_calls=2, max_chars=15_000)),
    ),
    budget=TaskBudget(max_calls=2, max_chars=15_000),
)
