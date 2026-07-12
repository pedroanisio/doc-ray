"""The webapp's one write path: every mutation goes through the GraphQL API.

The browser stays read-only over Postgres; writes are proxied here so there is
exactly one write path (mutations enqueue worker jobs — the webapp never
touches the queue or the registry directly).
"""

from __future__ import annotations

import os

from flask import has_request_context, request

from doc_ray.correlation import REQUEST_ID_HEADER, ensure_request_id_from_headers, new_request_id

API_URL = os.environ.get("DOCRAY_API_URL", "http://localhost:8000/graphql")

# The API's mutations always require a write-scoped principal (anonymous callers
# are read-only regardless of DOCRAY_API_ALLOW_ANONYMOUS — see
# entrypoints/api/mutation.py `_require_write`), so every write forwarded to it
# needs this credential. Unset => mutations fail with a 200-wrapped GraphQL
# PermissionError, surfaced to the browser by the callers below.
API_TOKEN = os.environ.get("DOCRAY_WEBAPP_API_TOKEN") or None


def api_auth_headers() -> dict[str, str]:
    """Auth plus request-id headers for API calls."""
    headers = {"Authorization": f"Bearer {API_TOKEN}"} if API_TOKEN else {}
    headers[REQUEST_ID_HEADER] = (
        ensure_request_id_from_headers(request.headers)
        if has_request_context()
        else new_request_id()
    )
    return headers


def gql(query: str, variables: dict | None = None) -> dict:
    """POST one GraphQL operation to the API and return its ``data`` payload.

    Raises:
        RuntimeError: If the API reports GraphQL errors.
    """
    import httpx

    response = httpx.post(
        API_URL,
        json={"query": query, "variables": variables or {}},
        headers=api_auth_headers(),
        timeout=30.0,
    )
    response.raise_for_status()
    body = response.json()
    if body.get("errors"):
        raise RuntimeError(body["errors"][0].get("message", "GraphQL error"))
    return body["data"]
