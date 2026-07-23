"""Per-tool-call audit logging for the MCP layer.

Every tool invocation emits one INFO line on the ``doc_ray.mcp.audit`` logger:
the tool name, a non-reversible caller fingerprint (never the bearer token
itself), the outcome (``ok`` or the typed error kind), the duration, and
clipped argument summaries. This is the MCP boundary's activity log — the
GraphQL API logs its own side — so an operator can answer "who called what,
when, and did it work" without reconstructing it from transport logs.
"""

from __future__ import annotations

import functools
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any  # noqa: F401 — used in the public alias below

from doc_ray.entrypoints.mcp import corpus, tokens

#: Signature of an async MCP tool as registered on the server.
Tool = Callable[..., Awaitable[Any]]

logger = logging.getLogger("doc_ray.mcp.audit")

#: Cap on each logged argument value so audit lines stay single-line and never
#: mirror a full tool payload into the log.
_MAX_ARG_CHARS = 80


def _caller() -> str:
    """A stable, non-reversible fingerprint of the caller's bearer token."""
    token = tokens.current_token()
    if not token:
        return "anonymous"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def _args_summary(kwargs: dict[str, object]) -> str:
    """Render call arguments as clipped ``name=value`` pairs."""
    parts = []
    for name, value in kwargs.items():
        rendered = repr(value)
        if len(rendered) > _MAX_ARG_CHARS:
            rendered = f"{rendered[:_MAX_ARG_CHARS]}…[+{len(rendered) - _MAX_ARG_CHARS}]"
        parts.append(f"{name}={rendered}")
    return " ".join(parts)


def audited(tool: Tool) -> Tool:
    """Wrap an async MCP tool so every call emits one audit log line.

    ``functools.wraps`` preserves the name, docstring, annotations, and (via
    ``__wrapped__``) the signature that FastMCP derives the tool schema from,
    so wrapping is invisible to MCP clients.
    """

    @functools.wraps(tool)
    async def wrapper(*args: object, **kwargs: object) -> object:
        started = time.monotonic()
        outcome, error = "ok", ""
        try:
            return await tool(*args, **kwargs)
        except corpus.ToolCallError as exc:
            outcome, error = "error", f" error={exc.kind}"
            raise
        except Exception as exc:
            outcome, error = "error", f" error={type(exc).__name__}"
            raise
        finally:
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.info(
                "tool=%s caller=%s outcome=%s%s duration_ms=%d %s",
                tool.__name__,
                _caller(),
                outcome,
                error,
                duration_ms,
                _args_summary(kwargs),
            )

    return wrapper
