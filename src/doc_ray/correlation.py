"""Request/correlation-id helpers shared by API, webapp, queue, and worker."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from contextvars import ContextVar, Token
from typing import Any

REQUEST_ID_HEADER = "X-Request-ID"
CORRELATION_ID_HEADER = "X-Correlation-ID"

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_CURRENT_REQUEST_ID: ContextVar[str | None] = ContextVar("docray_request_id", default=None)


def new_request_id() -> str:
    """Return a fresh request id suitable for logs and headers."""
    return f"req-{uuid.uuid4().hex}"


def normalize_request_id(raw: object) -> str | None:
    """Return a safe request id from untrusted input, or ``None``."""
    if not isinstance(raw, str):
        return None
    request_id = raw.strip()
    if not request_id or not _SAFE_REQUEST_ID.fullmatch(request_id):
        return None
    return request_id


def request_id_from_headers(headers: Mapping[str, str]) -> str | None:
    """Extract an incoming request id from standard correlation headers."""
    return normalize_request_id(
        headers.get(REQUEST_ID_HEADER)
        or headers.get(REQUEST_ID_HEADER.lower())
        or headers.get(CORRELATION_ID_HEADER)
        or headers.get(CORRELATION_ID_HEADER.lower())
    )


def ensure_request_id(raw: object | None = None) -> str:
    """Return a normalized request id, generating one when needed."""
    return normalize_request_id(raw) or new_request_id()


def ensure_request_id_from_headers(headers: Mapping[str, str]) -> str:
    """Extract or generate a request id for a request boundary."""
    return request_id_from_headers(headers) or new_request_id()


def current_request_id() -> str | None:
    """Return the request id bound to this execution context, if any."""
    return _CURRENT_REQUEST_ID.get()


def set_current_request_id(request_id: str) -> Token[str | None]:
    """Bind ``request_id`` to the current execution context."""
    return _CURRENT_REQUEST_ID.set(ensure_request_id(request_id))


def reset_current_request_id(token: Token[str | None]) -> None:
    """Restore the previous request-id context."""
    _CURRENT_REQUEST_ID.reset(token)


def payload_with_request_metadata(
    payload: Mapping[str, Any], request_id: str | None = None
) -> dict[str, Any]:
    """Return ``payload`` copied with ``_meta.request_id`` when one is active."""
    copied = dict(payload)
    request_id = normalize_request_id(request_id) or current_request_id()
    if not request_id:
        return copied
    meta = dict(copied.get("_meta") or {})
    meta["request_id"] = request_id
    copied["_meta"] = meta
    return copied


def request_id_from_payload(payload: Mapping[str, Any]) -> str | None:
    """Return ``_meta.request_id`` from a job payload, if present and safe."""
    meta = payload.get("_meta")
    if not isinstance(meta, Mapping):
        return None
    return normalize_request_id(meta.get("request_id"))
