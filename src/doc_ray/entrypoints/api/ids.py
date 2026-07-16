"""Shared id-validation helpers for the GraphQL resolvers."""

from __future__ import annotations

import uuid


def clean_uuid(value: str) -> str | None:
    """Normalize *value* to a canonical uuid string, or ``None`` if malformed.

    A ``uuid``-typed column raises ``invalid input syntax for type uuid`` on
    comparison with a malformed value (unlike the ``ulid`` domain, whose CHECK
    constraint only fires on an explicit cast, not a ``=`` comparison) — every
    resolver taking a document/job id must validate before querying, or that raw
    Postgres exception text reaches the caller verbatim (BL-110).
    """
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None
