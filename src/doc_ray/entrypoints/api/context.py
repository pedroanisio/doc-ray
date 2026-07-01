"""The per-request GraphQL context shared by every resolver."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg
from strawberry.fastapi import BaseContext

from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.security import Principal

#: Hard cap on any per-field ``limit`` argument, so a single query can't ask for
#: an unbounded slice (defence-in-depth alongside the depth/complexity limits).
MAX_PAGE_SIZE = 200


@dataclass
class RequestContext(BaseContext):
    """Everything a resolver needs for one request.

    Inherits :class:`strawberry.fastapi.BaseContext` so Strawberry's FastAPI
    integration accepts it as a custom context (it rejects any context that is
    neither a ``BaseContext`` subclass nor a dict). Strawberry sets ``request``/
    ``response``/``background_tasks`` on the instance after construction.

    Attributes:
        conn: The request's pooled async connection (RLS principal already set).
        principal: The authenticated identity for the request.
        loaders: Fresh per-request by-document DataLoaders sharing ``conn``.
    """

    conn: psycopg.AsyncConnection[dict[str, Any]]
    principal: Principal
    loaders: Loaders

    def page_size(self, requested: int) -> int:
        """Clamp a requested page size to ``[1, MAX_PAGE_SIZE]``."""
        return max(1, min(requested, MAX_PAGE_SIZE))
