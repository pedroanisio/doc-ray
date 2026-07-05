"""Small helpers for opaque read-model keyset cursors."""

from __future__ import annotations

import base64
import json
from datetime import date, datetime
from typing import Any, cast


def _json_default(value: object) -> str:
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def encode_cursor(kind: str, **parts: object) -> str:
    """Encode keyset parts into a URL-safe cursor string."""
    payload = {"kind": kind, "parts": parts}
    raw = json.dumps(payload, default=_json_default, sort_keys=True, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None, kind: str) -> dict[str, Any] | None:
    """Decode a cursor, returning ``None`` when no cursor was provided."""
    if cursor is None or cursor == "":
        return None
    padded = cursor + "=" * (-len(cursor) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    if payload.get("kind") != kind or not isinstance(payload.get("parts"), dict):
        raise ValueError(f"cursor is not a {kind} cursor")
    return cast(dict[str, Any], payload["parts"])
