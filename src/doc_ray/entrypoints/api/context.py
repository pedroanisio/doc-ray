"""The per-request GraphQL context shared by every resolver."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import psycopg
from strawberry.fastapi import BaseContext

from doc_ray.config import Config
from doc_ray.correlation import new_request_id
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.security import Principal

#: Hard cap on any per-field ``limit`` argument, so a single query can't ask for
#: an unbounded slice (defence-in-depth alongside the depth/complexity limits).
MAX_PAGE_SIZE = 200

#: Hard cap on any batch id-array mutation argument (e.g. decideSeeds's
#: approveIds/rejectIds). Unlike MAX_PAGE_SIZE this is never silently clamped —
#: a caller over the cap gets a clean rejection, since silently dropping ids
#: from a write batch would mean some approve/reject the caller asked for
#: never happened (BL-109).
MAX_BATCH_IDS = 500


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
        request_id: Correlation id for logs and queued job metadata.
        settings: Runtime configuration for resolvers that need deployment
            settings rather than only database access.
    """

    conn: psycopg.AsyncConnection[dict[str, Any]]
    principal: Principal
    loaders: Loaders
    request_id: str = field(default_factory=new_request_id)
    settings: Config | None = None

    def page_size(self, requested: int) -> int:
        """Clamp a requested page size to ``[1, MAX_PAGE_SIZE]``."""
        return max(1, min(requested, MAX_PAGE_SIZE))
