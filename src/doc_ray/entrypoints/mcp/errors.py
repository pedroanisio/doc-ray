"""Typed MCP failures and local argument validation shared by read/write tools."""

from __future__ import annotations

import uuid


class ToolCallError(RuntimeError):
    """Stable MCP failure kind with retriability and an optional agent hint."""

    def __init__(
        self, kind: str, message: str, *, retriable: bool = False, hint: str | None = None
    ) -> None:
        """Render the stable kind, retry marker, and optional recovery hint."""
        self.kind = kind
        self.retriable = retriable
        self.hint = hint
        rendered = f"[{kind}] {message}"
        if retriable:
            rendered += " (retriable)"
        if hint:
            rendered += f" — {hint}"
        super().__init__(rendered)


def require_uuid(value: str | None, param: str) -> str | None:
    """Reject a malformed document id before it costs a GraphQL round-trip."""
    if value is None:
        return None
    try:
        uuid.UUID(str(value))
    except ValueError:
        raise ToolCallError(
            "bad_input",
            f"{param} must be a document UUID, got {value!r}",
            hint="take ids from list_documents or full_text_search",
        ) from None
    return value


def require_nonempty(value: str, param: str) -> str:
    """Reject blank required strings before any network call."""
    if not value or not value.strip():
        raise ToolCallError("bad_input", f"{param} must be a non-empty string")
    return value


RESPONSE_FORMATS = ("detailed", "concise")


def require_response_format(value: str) -> str:
    """Reject an unknown response format locally and name both valid values."""
    if value not in RESPONSE_FORMATS:
        raise ToolCallError(
            "bad_input",
            f"response_format must be one of {', '.join(RESPONSE_FORMATS)}, got {value!r}",
            hint="use 'concise' to drop per-row metadata, 'detailed' for the full shape",
        )
    return value
