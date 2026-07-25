"""Runner for the MCP evaluation harness (issue #12).

ARCHITECTURAL REQUIREMENT (PALS's LAW): LLMs will always produce some form of error.
Absence of output verification is a design defect, not a runtime bug.
All LLM output must be treated as untrusted and validated explicitly.

This module measures the agent-facing MCP tool surface *empirically* rather than
asserting it. It calls the real tool callables (``corpus``/``structure``/
``compose``) exactly as an agent would, records the transcript, and scores it
with the pure predicates in :mod:`doc_ray.domain.mcp_eval_scoring`.

Two execution modes (FR-5):

* ``scripted`` — a fixed, hand-written call sequence per task. Deterministic,
  free, CI-safe. This is what measures the *surface*: payload sizes, error
  shapes, and whether the data an agent needs is reachable in N calls. The
  harness *is* the scripted agent, executing the intended-correct strategy so a
  regression that breaks the surface shows up as a failed assertion.
* ``agentic`` — a real model drives the tools. It measures *behaviour*, not the
  surface. It is **not implemented in this build** (Open decision #2: no model
  credentials are provisioned here) and raises :class:`EvalModeUnsupported`
  naming exactly what is missing — never a silent no-op.

**Transport.** The tools forward a GraphQL query over ``httpx`` to the API. The
harness has no API server, so :func:`tool_transport` routes that POST to the
Strawberry schema executed **in-process** against the test database, scoped to a
principal — the same schema an agent's calls would hit, with the real
token-forwarding/error-mapping path in ``corpus.gql`` still exercised (that is
where the cursor-steering hint is produced). BL-144: the ``mcp`` streamable-HTTP
module is imported eagerly before ``httpx`` is patched.

The harness depends on ``entrypoints`` on purpose — those tool functions are the
unit under test. It seeds and destroys its own fixtures and never writes to a
corpus it did not create (see :mod:`doc_ray.application.mcp_eval_fixtures`).
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import psycopg
import strawberry
from psycopg.rows import dict_row

from doc_ray.config import DEFAULT_DATABASE_URL
from doc_ray.domain import mcp_eval_scoring as scoring
from doc_ray.domain.mcp_eval_scoring import AssertionResult, RecordedCall, TaskBudget
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.schema import build_schema
from doc_ray.entrypoints.api.security import Principal
from doc_ray.entrypoints.mcp.corpus import ToolCallError

_DB_URL = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)

#: An async connection scoped to a principal, and a factory for one.
AConn = psycopg.AsyncConnection[dict[str, Any]]
ConnFactory = Callable[[], Awaitable[AConn]]

#: A fixed admin principal for the harness. Admin so RLS surfaces every fixture
#: the harness seeds, keeping the measurement about the tool surface rather than
#: about ownership (RLS enforcement is proven by the test_*_rls suites).
HARNESS_PRINCIPAL_ID = "00000000-0000-0000-0000-0000000000e5"
HARNESS_PRINCIPAL = Principal(
    subject="mcp-eval-harness",
    principal_id=HARNESS_PRINCIPAL_ID,
    scopes=frozenset({"read"}),
)


class EvalModeUnsupported(RuntimeError):
    """Raised when an evaluation mode cannot run in this build (typed, never silent)."""


# ---------------------------------------------------------------------------
# task model
# ---------------------------------------------------------------------------


@dataclass
class RunContext:
    """Mutable per-run state: seeded fixture ids and the accumulating transcript."""

    ids: dict[str, str]
    calls: list[RecordedCall] = field(default_factory=list)

    def last_response(self, tool_name: str) -> Any:  # noqa: ANN401 (tool payloads are dynamic)
        """The most recent successful response from *tool_name*, or ``None``."""
        for call in reversed(self.calls):
            if call.tool == tool_name and call.ok:
                return call.response
        return None

    def first_search_cursor(self) -> str | None:
        """The cursor of the first ``search_sentences`` hit, for the cursor task."""
        resp = next((c.response for c in self.calls if c.tool == "search_sentences" and c.ok), None)
        hits = (resp or {}).get("hits") if isinstance(resp, dict) else None
        cursor = hits[0].get("cursor") if hits else None
        return cursor if isinstance(cursor, str) else None


@dataclass(frozen=True)
class Assertion:
    """A named scoring predicate bound to its parameters."""

    name: str
    check: Callable[[tuple[RecordedCall, ...]], AssertionResult]


@dataclass(frozen=True)
class Step:
    """One scripted tool call: the tool callable and an argument builder."""

    tool: Callable[..., Awaitable[Any]]
    build_args: Callable[[RunContext], dict[str, Any]]
    #: True when this call is *supposed* to raise ``ToolCallError`` (e.g. the
    #: cursor dead-end). An unexpected raise on a ``False`` step fails the task.
    expect_error: bool = False


@dataclass(frozen=True)
class EvalTask:
    """A declarative evaluation task — a record, not a script (FR-1)."""

    id: str
    kind: str  # "grounded" (behavioural) | "coverage" (reachability smoke)
    prompt: str
    grounded_in: str
    steps: tuple[Step, ...]
    assertions: tuple[Assertion, ...]
    budget: TaskBudget

    @property
    def tools_touched(self) -> frozenset[str]:
        """The tool names this task exercises — the drift guard's coverage unit."""
        return frozenset(step.tool.__name__ for step in self.steps)


def validate_task(task: EvalTask) -> None:
    """Reject a malformed task at load: no silent, vacuously-passing tasks (FR-1)."""
    if not task.steps:
        raise ValueError(f"task {task.id!r} has no steps")
    if not task.assertions:
        raise ValueError(f"task {task.id!r} has an empty assertion tuple")
    if task.kind not in ("grounded", "coverage"):
        raise ValueError(f"task {task.id!r} has unknown kind {task.kind!r}")


# ---------------------------------------------------------------------------
# in-process transport: httpx POST -> Strawberry schema against the test DB
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._body


def _make_client(
    schema: strawberry.Schema, principal: Principal, aconn_factory: ConnFactory
) -> type:
    class _Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        async def post(
            self,
            url: str,
            *,
            json: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
        ) -> _FakeResponse:
            payload = json or {}
            aconn = await aconn_factory()
            try:
                ctx = RequestContext(conn=aconn, principal=principal, loaders=Loaders(aconn))
                result = await schema.execute(
                    payload.get("query", ""),
                    variable_values=payload.get("variables") or {},
                    context_value=ctx,
                )
            finally:
                await aconn.close()
            if result.errors:
                return _FakeResponse(
                    {"errors": [{"message": getattr(e, "message", str(e))} for e in result.errors]}
                )
            return _FakeResponse({"data": result.data})

    return _Client


@contextlib.asynccontextmanager
async def tool_transport(
    *, schema: strawberry.Schema, principal: Principal, aconn_factory: ConnFactory
) -> AsyncIterator[None]:
    """Route the tools' ``httpx`` POSTs to the in-process schema for the duration."""
    import httpx

    # BL-144: import the mcp streamable-http module (which evaluates
    # ``httpx.AsyncClient | None`` at import time) BEFORE reassigning AsyncClient.
    import mcp.client.streamable_http  # noqa: F401

    original = httpx.AsyncClient
    httpx.AsyncClient = _make_client(schema, principal, aconn_factory)  # type: ignore[misc,assignment]
    try:
        yield
    finally:
        httpx.AsyncClient = original  # type: ignore[misc]


async def default_aconn_factory() -> AConn:
    """An async connection scoped to ``docray_api`` as the harness principal.

    Mirrors ``entrypoints/api/security.py``'s per-request scoping so the schema
    executes under the same role/principal an agent's token would resolve to.
    """
    conn = await psycopg.AsyncConnection.connect(_DB_URL, row_factory=dict_row, autocommit=True)
    await conn.execute("SET ROLE docray_api")
    await conn.execute("SELECT set_config('app.principal_id', %s, false)", (HARNESS_PRINCIPAL_ID,))
    return conn


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------


def _measure_chars(response: object) -> int:
    return len(json.dumps(response, default=str, ensure_ascii=False))


async def _invoke(step: Step, ctx: RunContext) -> RecordedCall:
    """Call one step's tool, recording the outcome (success or typed failure)."""
    kwargs = step.build_args(ctx)
    started = time.perf_counter()
    try:
        response = await step.tool(**kwargs)
    except ToolCallError as exc:
        return RecordedCall(
            tool=step.tool.__name__,
            args=kwargs,
            ok=False,
            error_kind=exc.kind,
            error_hint=exc.hint,
            duration_s=time.perf_counter() - started,
        )
    return RecordedCall(
        tool=step.tool.__name__,
        args=kwargs,
        ok=True,
        response=response,
        response_chars=_measure_chars(response),
        duration_s=time.perf_counter() - started,
    )


#: Called with (task_id, RecordedCall) after each step, for --verbose observability.
Observer = Callable[[str, RecordedCall], None]


async def score_task(
    task: EvalTask, ctx: RunContext, *, observer: Observer | None = None
) -> dict[str, Any]:
    """Run *task*'s steps against the already-seeded ``ctx`` and score the result.

    An unexpected ``ToolCallError`` (a step not marked ``expect_error``) fails the
    task *loudly* — a synthetic failed assertion naming the tool and error — never
    scored as passed-with-zero-calls. That is the difference between a ruler and a
    rubber stamp.
    """
    validate_task(task)
    calls: list[RecordedCall] = []
    tool_fault: str | None = None
    for step in task.steps:
        call = await _invoke(step, ctx)
        calls.append(call)
        ctx.calls.append(call)
        if observer is not None:
            observer(task.id, call)
        if not call.ok and not step.expect_error and tool_fault is None:
            tool_fault = f"{call.tool} raised [{call.error_kind}]: {call.error_hint}"
    if tool_fault is not None:
        results: tuple[AssertionResult, ...] = (
            AssertionResult("no_unexpected_tool_error", False, tool_fault),
        )
    else:
        results = tuple(a.check(tuple(calls)) for a in task.assertions)
    return scoring.task_report(task.id, task.grounded_in, tuple(calls), results, task.budget)


async def run_scripted(
    tasks: tuple[EvalTask, ...],
    world_ids: dict[str, str],
    *,
    schema: strawberry.Schema | None = None,
    principal: Principal = HARNESS_PRINCIPAL,
    aconn_factory: ConnFactory | None = None,
    observer: Observer | None = None,
) -> list[dict[str, Any]]:
    """Run every task in ``scripted`` mode against the seeded world; return reports."""
    schema = schema if schema is not None else build_schema()
    factory = aconn_factory or default_aconn_factory
    reports: list[dict[str, Any]] = []
    async with tool_transport(schema=schema, principal=principal, aconn_factory=factory):
        for task in tasks:
            ctx = RunContext(ids=dict(world_ids))
            reports.append(await score_task(task, ctx, observer=observer))
    return reports


def unsupported_agentic() -> EvalModeUnsupported:
    """The typed failure for ``--mode agentic`` (Open decision #2, FR-5).

    Agentic mode is not implemented in this build: no model client is wired and no
    API credentials are provisioned in this environment. The switch fails with a
    clear, typed message naming exactly what is missing, rather than a silent
    no-op. Implementing it later means wiring a model client and registering its
    key in ``config/env.registry.yaml``.
    """
    return EvalModeUnsupported(
        "agentic mode is not implemented in this build: it requires wiring a model "
        "client and provisioning its API key (register the key in "
        "config/env.registry.yaml). Use --mode scripted, which measures the tool "
        "surface deterministically and needs no model."
    )
