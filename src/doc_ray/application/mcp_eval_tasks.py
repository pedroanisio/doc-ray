"""The MCP evaluation task set (issue #12, FR-2).

Two tiers, all declarative records (never scripts):

* **grounded** — the six tasks FR-2 mandates, each derived from a failure this
  programme actually observed, scored by a behavioural predicate.
* **coverage** — reachability smoke tasks that exercise every *other* registered
  read tool at least once, so the drift guard (``test_mcp_eval_tasks.py``) stays
  green only while the whole surface is measured. A coverage task asserts the
  tool ran and returned the documented shape — even when the fixture legitimately
  yields an empty result (a document with no images), which is honest coverage of
  the validation/GraphQL/shaping path, not a behavioural claim.

Every task runs against the disposable fixture world seeded by
:mod:`doc_ray.application.mcp_eval_fixtures`; the ordinal landmarks come from
that module so the fixtures and the assertions cannot drift.
"""

from __future__ import annotations

from doc_ray.application.mcp_eval import Assertion, EvalTask, RunContext, Step, validate_task
from doc_ray.application.mcp_eval_fixtures import (
    ENTITY_SURFACE,
    FLOOD_TERM,
    MAIN_SENTENCE_COUNT,
    RARE_ORDINALS,
    RARE_TERM,
)
from doc_ray.domain import mcp_eval_scoring as scoring
from doc_ray.domain.mcp_eval_scoring import TaskBudget
from doc_ray.entrypoints.mcp import compose, corpus, semantic, structure

#: A fixed, valid-form ULID for a lexeme that does not exist — lexeme_kwic returns
#: an empty concordance for it, which still exercises the tool end to end.
_MISSING_LEXEME_UID = "01K5MCPEVA0000000000000000"

# Rare-term window: the term sits at RARE_ORDINALS; allow a small margin.
_RARE_LO = min(RARE_ORDINALS) - 2
_RARE_HI = max(RARE_ORDINALS) + 2


def _main(ctx: RunContext) -> str:
    return ctx.ids["doc_main"]


def _entity(ctx: RunContext) -> str:
    return ctx.ids["doc_entity"]


def _within(budget: TaskBudget) -> Assertion:
    return Assertion("within_budget", lambda calls: scoring.within_budget(calls, budget))


def _ok(tool: str) -> Assertion:
    return Assertion(f"{tool}_ok", lambda calls: scoring.check_tool_ok(calls, tool))


def _nonempty(tool: str) -> Assertion:
    return Assertion(f"{tool}_nonempty", lambda calls: scoring.check_tool_nonempty(calls, tool))


def _tail_from(ctx: RunContext) -> int:
    outline = ctx.last_response("read_document_outline") or {}
    total = outline.get("totalSentences") or MAIN_SENTENCE_COUNT
    return max(0, total - 5)


# --- grounded tasks (FR-2) --------------------------------------------------

_COVER = EvalTask(
    id="cover-document-exhaustively",
    kind="grounded",
    prompt="Read this entire document; summarise it without missing the back half.",
    grounded_in="the reviewer ran out of budget on the back half of a novel",
    steps=(
        Step(structure.read_document_outline, lambda ctx: {"document_id": _main(ctx)}),
        Step(
            corpus.read_document_sentences,
            lambda ctx: {"document_id": _main(ctx), "from_ordinal": 0, "to_ordinal": 19},
        ),
        Step(
            corpus.read_document_sentences,
            lambda ctx: {"document_id": _main(ctx), "from_ordinal": 20, "to_ordinal": 39},
        ),
    ),
    assertions=(
        Assertion("coverage>=0.99", lambda calls: scoring.check_coverage_at_least(calls, 0.99)),
        _within(TaskBudget(max_calls=4, max_chars=20_000)),
    ),
    budget=TaskBudget(max_calls=4, max_chars=20_000),
)

_RARE = EvalTask(
    id="find-rare-scene",
    kind="grounded",
    prompt=f"Find where {RARE_TERM!r} appears; give the region.",
    grounded_in="the search_sentences rare-term guidance (Item 1)",
    steps=(
        Step(
            corpus.search_sentences,
            lambda ctx: {"term": RARE_TERM, "document_id": _main(ctx), "limit": 20},
        ),
    ),
    assertions=(
        Assertion(
            "hit_in_region",
            lambda calls: scoring.check_search_hit_in_range(calls, _RARE_LO, _RARE_HI),
        ),
        _within(TaskBudget(max_calls=2, max_chars=6_000)),
    ),
    budget=TaskBudget(max_calls=2, max_chars=6_000),
)

_FLOOD = EvalTask(
    id="survive-keyword-flood",
    kind="grounded",
    prompt=f"Search {FLOOD_TERM!r}; is the rest of the document really empty?",
    grounded_in="the BL-145 keyword-flood failure: a flooded region read as missing content",
    steps=(
        Step(
            corpus.search_sentences,
            lambda ctx: {"term": FLOOD_TERM, "document_id": _main(ctx), "limit": 20},
        ),
        Step(
            corpus.read_document_sentences,
            lambda ctx: {"document_id": _main(ctx), "from_ordinal": 30, "to_ordinal": 39},
        ),
    ),
    assertions=(
        Assertion("flood_flagged", scoring.check_flood_flagged),
        Assertion("content_reachable", scoring.check_content_reachable_after_flood),
        _within(TaskBudget(max_calls=3, max_chars=15_000)),
    ),
    budget=TaskBudget(max_calls=3, max_chars=15_000),
)

_ENDING = EvalTask(
    id="read-the-ending",
    kind="grounded",
    prompt="Read the ending of this document.",
    grounded_in="Item 5's reason for existing: address the final region, don't page from 0",
    steps=(
        Step(structure.read_document_outline, lambda ctx: {"document_id": _main(ctx)}),
        Step(
            corpus.read_document_sentences,
            lambda ctx: {
                "document_id": _main(ctx),
                "from_ordinal": _tail_from(ctx),
                "to_ordinal": MAIN_SENTENCE_COUNT - 1,
            },
        ),
    ),
    assertions=(
        Assertion("addresses_ending", lambda calls: scoring.check_addresses_ending(calls, 0.15)),
        _within(TaskBudget(max_calls=3, max_chars=10_000)),
    ),
    budget=TaskBudget(max_calls=3, max_chars=10_000),
)

_TYPE_SPLIT = EvalTask(
    id="entity-roster-not-fooled-by-type-split",
    kind="grounded",
    prompt=f"How many times is {ENTITY_SURFACE} mentioned, and what type is it?",
    grounded_in="Item 6's reason for existing: NER splits one entity across types",
    steps=(Step(structure.entity_profiles, lambda ctx: {"document_id": _entity(ctx)}),),
    assertions=(
        Assertion(
            "one_contested_row",
            lambda calls: scoring.check_single_contested_profile(calls, ENTITY_SURFACE, 2),
        ),
        _within(TaskBudget(max_calls=2, max_chars=8_000)),
    ),
    budget=TaskBudget(max_calls=2, max_chars=8_000),
)

_CURSOR = EvalTask(
    id="cursor-not-interchangeable",
    kind="grounded",
    prompt="Keep reading past the search results using the cursor.",
    grounded_in="the cursor dead-end in BL-145: a search cursor handed to the reader",
    steps=(
        Step(
            corpus.search_sentences,
            lambda ctx: {"term": FLOOD_TERM, "document_id": _main(ctx), "limit": 5},
        ),
        Step(
            corpus.read_document_sentences,
            lambda ctx: {"document_id": _main(ctx), "after": ctx.first_search_cursor()},
            expect_error=True,
        ),
        Step(
            corpus.read_document_sentences,
            lambda ctx: {"document_id": _main(ctx), "from_ordinal": 0, "to_ordinal": 4},
        ),
    ),
    assertions=(
        Assertion("cursor_steered", scoring.check_cursor_dead_end_steered),
        Assertion("recovered", lambda calls: scoring.check_recovered_after_cursor(calls, 1)),
        _within(TaskBudget(max_calls=4, max_chars=12_000)),
    ),
    budget=TaskBudget(max_calls=4, max_chars=12_000),
)

# --- coverage tasks (drift guard: every remaining read tool exercised once) --

_DISCOVER = EvalTask(
    id="coverage-document-discovery",
    kind="coverage",
    prompt="Which document is about the spice, and what is in it?",
    grounded_in="reachability: full_text_search / list_documents / get_document",
    # Small limits so transient documents from other suites (when run inside the
    # full pytest suite) cannot push these corpus-wide tools over budget; the clean
    # baseline corpus has only two documents, so the committed metrics are unchanged.
    steps=(
        Step(corpus.full_text_search, lambda ctx: {"query": FLOOD_TERM, "limit": 5}),
        Step(corpus.list_documents, lambda ctx: {"limit": 5}),
        Step(corpus.get_document, lambda ctx: {"document_id": _main(ctx)}),
    ),
    assertions=(
        _nonempty("full_text_search"),
        _nonempty("list_documents"),
        _nonempty("get_document"),
        _within(TaskBudget(max_calls=4, max_chars=30_000)),
    ),
    budget=TaskBudget(max_calls=4, max_chars=30_000),
)

_COLLECTIONS = EvalTask(
    id="coverage-collections-and-lenses",
    kind="coverage",
    prompt="What collections and resolution lenses exist?",
    grounded_in="reachability: list_collections / get_collection / list_lenses",
    steps=(
        Step(corpus.list_collections, lambda ctx: {"limit": 50}),
        Step(corpus.get_collection, lambda ctx: {"collection_id": ctx.ids["collection_id"]}),
        Step(corpus.list_lenses, lambda ctx: {"limit": 50}),
    ),
    assertions=(
        _nonempty("list_collections"),
        _ok("get_collection"),
        _nonempty("list_lenses"),
        _within(TaskBudget(max_calls=4, max_chars=20_000)),
    ),
    budget=TaskBudget(max_calls=4, max_chars=20_000),
)

_MAP = EvalTask(
    id="coverage-corpus-map",
    kind="coverage",
    prompt="Shard the corpus for a map-reduce sweep.",
    grounded_in="reachability: corpus_manifest / corpus_shard",
    # shard_count=1 (all documents in one shard) keeps the payload deterministic
    # across independent seedings: sharding is hashtext(id) mod N over random per-
    # seed uuids, so a wider split would move documents between shards run to run.
    steps=(
        Step(corpus.corpus_manifest, lambda ctx: {"shard_count": 1}),
        Step(corpus.corpus_shard, lambda ctx: {"shard_index": 0, "shard_count": 1, "limit": 5}),
    ),
    assertions=(
        _ok("corpus_manifest"),
        _ok("corpus_shard"),
        _within(TaskBudget(max_calls=3, max_chars=20_000)),
    ),
    budget=TaskBudget(max_calls=3, max_chars=20_000),
)

_ENTITIES = EvalTask(
    id="coverage-entities-and-lexemes",
    kind="coverage",
    prompt="Who appears in the entity document, and any corrections?",
    grounded_in="reachability: entity_frequency / lexeme_kwic / entity_mention_corrections",
    steps=(
        Step(corpus.entity_frequency, lambda ctx: {"document_id": _entity(ctx), "limit": 50}),
        Step(corpus.lexeme_kwic, lambda ctx: {"uid": _MISSING_LEXEME_UID, "limit": 40}),
        Step(corpus.entity_mention_corrections, lambda ctx: {"document_id": _entity(ctx)}),
    ),
    assertions=(
        _nonempty("entity_frequency"),
        _ok("lexeme_kwic"),
        _ok("entity_mention_corrections"),
        _within(TaskBudget(max_calls=4, max_chars=20_000)),
    ),
    budget=TaskBudget(max_calls=4, max_chars=20_000),
)

_JOBS = EvalTask(
    id="coverage-job-status",
    kind="coverage",
    prompt="What is the status of the ingest job?",
    grounded_in="reachability: get_job",
    steps=(Step(corpus.get_job, lambda ctx: {"job_id": ctx.ids["job_id"]}),),
    assertions=(_nonempty("get_job"), _within(TaskBudget(max_calls=2, max_chars=8_000))),
    budget=TaskBudget(max_calls=2, max_chars=8_000),
)

_VISUAL = EvalTask(
    id="coverage-visual-surface",
    kind="coverage",
    prompt="Are there images or captions in this document?",
    grounded_in="reachability: the five Prism visual read tools",
    steps=(
        Step(corpus.document_images, lambda ctx: {"document_id": _main(ctx), "limit": 100}),
        Step(corpus.image_class_summary, lambda ctx: {"document_id": _main(ctx)}),
        Step(corpus.cross_modal_image_search, lambda ctx: {"query": "desert", "limit": 25}),
        Step(corpus.visual_captions, lambda ctx: {"document_id": _main(ctx), "limit": 100}),
        Step(corpus.visual_review, lambda ctx: {"document_id": _main(ctx), "limit": 100}),
    ),
    assertions=(
        Assertion("all_calls_ok", scoring.check_all_ok),
        _within(TaskBudget(max_calls=6, max_chars=20_000)),
    ),
    budget=TaskBudget(max_calls=6, max_chars=20_000),
)

_COMPOSE = EvalTask(
    id="coverage-composition-grounding",
    kind="coverage",
    prompt="Assemble a cited evidence pack about the spice and about Arrakis.",
    grounded_in="reachability: briefing_pack / entity_dossier / document_evidence",
    steps=(
        Step(compose.briefing_pack, lambda ctx: {"topic": FLOOD_TERM, "max_documents": 3}),
        Step(compose.entity_dossier, lambda ctx: {"name": ENTITY_SURFACE, "max_mentions": 10}),
        Step(
            compose.document_evidence,
            lambda ctx: {"document_id": _main(ctx), "focus": FLOOD_TERM, "max_passages": 6},
        ),
    ),
    assertions=(
        _ok("briefing_pack"),
        _ok("entity_dossier"),
        _ok("document_evidence"),
        _within(TaskBudget(max_calls=4, max_chars=40_000)),
    ),
    budget=TaskBudget(max_calls=4, max_chars=40_000),
)

_SEMANTIC = EvalTask(
    id="coverage-semantic-search",
    kind="coverage",
    prompt="Find sentences about the spice by meaning, not exact keyword.",
    # semantic search is deployment-optional (NLP Layer 20); scoped to doc_main,
    # which carries no embeddings in the fixture, so the tool returns an empty
    # result where the model stack is installed and a typed ``unsupported`` where
    # it is not — both count as reachable, and both are deterministic.
    grounded_in="reachability: semantic_search_sentences (Layer 20, deployment-optional)",
    steps=(
        Step(
            semantic.semantic_search_sentences,
            lambda ctx: {
                "query": "desert power and the spice",
                "document_id": _main(ctx),
                "limit": 5,
            },
        ),
    ),
    assertions=(
        Assertion(
            "semantic_reachable",
            lambda calls: scoring.check_tool_reachable(calls, "semantic_search_sentences"),
        ),
        _within(TaskBudget(max_calls=2, max_chars=15_000)),
    ),
    budget=TaskBudget(max_calls=2, max_chars=15_000),
)

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

#: The full task set. Grounded tasks first, then coverage.
TASKS: tuple[EvalTask, ...] = (
    _COVER,
    _RARE,
    _FLOOD,
    _ENDING,
    _TYPE_SPLIT,
    _CURSOR,
    _DISCOVER,
    _COLLECTIONS,
    _MAP,
    _ENTITIES,
    _JOBS,
    _VISUAL,
    _COMPOSE,
    _SEMANTIC,
    _TERMFREQ,
)

for _task in TASKS:
    validate_task(_task)
