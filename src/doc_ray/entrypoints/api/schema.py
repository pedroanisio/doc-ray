"""Build the Strawberry schema with query-safety extensions (D-008).

The document graph is deeply recursive (a document's sentences, a lexeme's
occurrences pointing back at documents, ...), so the schema enforces a maximum
query depth and a maximum token count (a proxy for query complexity) alongside the
per-field page-size caps in the resolvers. Limits are overridable via ``API_MAX_DEPTH``
and ``API_MAX_TOKENS``.

Introspection is disabled unless ``DOCRAY_API_DEBUG`` opts in (BL-108): an
anonymous caller (the default per ``DOCRAY_API_ALLOW_ANONYMOUS``) should not be
able to enumerate the full schema — including admin-only mutation argument
shapes — for free reconnaissance.
"""

from __future__ import annotations

import os

import strawberry
from strawberry.extensions import DisableIntrospection, MaxTokensLimiter, QueryDepthLimiter

from doc_ray.entrypoints.api.mutation import Mutation
from doc_ray.entrypoints.api.query import Query

DEFAULT_MAX_DEPTH = 12
DEFAULT_MAX_TOKENS = 2000


def _env_debug() -> bool:
    return os.environ.get("DOCRAY_API_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}


def build_schema(
    max_depth: int | None = None,
    max_tokens: int | None = None,
    debug: bool | None = None,
) -> strawberry.Schema:
    """Build the read-only schema with depth and token (complexity) limits.

    Args:
        max_depth: Maximum query nesting depth; defaults to ``API_MAX_DEPTH`` or 12.
        max_tokens: Maximum query token count; defaults to ``API_MAX_TOKENS`` or 2000.
        debug: Whether introspection is allowed; defaults to ``DOCRAY_API_DEBUG``
            (false unless explicitly opted in).

    Returns:
        A configured :class:`strawberry.Schema`.
    """
    depth = (
        max_depth
        if max_depth is not None
        else int(os.environ.get("API_MAX_DEPTH", DEFAULT_MAX_DEPTH))
    )
    tokens = (
        max_tokens
        if max_tokens is not None
        else int(os.environ.get("API_MAX_TOKENS", DEFAULT_MAX_TOKENS))
    )
    dbg = debug if debug is not None else _env_debug()
    # Strawberry wants extension *factories* (a fresh instance per request), which
    # is also what its type stub expects — so pass zero-arg callables, not instances.
    extensions = [
        lambda: QueryDepthLimiter(max_depth=depth),
        lambda: MaxTokensLimiter(max_token_count=tokens),
    ]
    if not dbg:
        extensions.append(lambda: DisableIntrospection())
    return strawberry.Schema(
        query=Query,
        mutation=Mutation,
        extensions=extensions,
    )
