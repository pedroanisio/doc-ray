"""GraphQL API entrypoint (Strawberry over the async read adapter, D-001).

A read-only GraphQL surface over the document store. ``create_app`` builds the
ASGI application; ``app`` is the module-level instance for
``uvicorn doc_ray.entrypoints.api.app:app``.
"""

from __future__ import annotations

from doc_ray.entrypoints.api.app import create_app

__all__ = ["create_app"]
