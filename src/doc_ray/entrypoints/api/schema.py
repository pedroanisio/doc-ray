"""Build the Strawberry schema with query-safety extensions (D-008).

The document graph is deeply recursive (a document's sentences, a lexeme's
occurrences pointing back at documents, ...), so the schema enforces a maximum
query depth and a maximum token count (a proxy for query complexity) alongside the
per-field page-size caps in the resolvers. Limits are overridable via ``API_MAX_DEPTH``
and ``API_MAX_TOKENS``.
"""

from __future__ import annotations

import os

import strawberry
from strawberry.extensions import MaxTokensLimiter, QueryDepthLimiter

from doc_ray.entrypoints.api.query import Query

DEFAULT_MAX_DEPTH = 12
DEFAULT_MAX_TOKENS = 2000


def build_schema(max_depth: int | None = None, max_tokens: int | None = None) -> strawberry.Schema:
    """Build the read-only schema with depth and token (complexity) limits.

    Args:
        max_depth: Maximum query nesting depth; defaults to ``API_MAX_DEPTH`` or 12.
        max_tokens: Maximum query token count; defaults to ``API_MAX_TOKENS`` or 2000.

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
    # Strawberry wants extension *factories* (a fresh instance per request), which
    # is also what its type stub expects — so pass zero-arg callables, not instances.
    return strawberry.Schema(
        query=Query,
        extensions=[
            lambda: QueryDepthLimiter(max_depth=depth),
            lambda: MaxTokensLimiter(max_token_count=tokens),
        ],
    )
