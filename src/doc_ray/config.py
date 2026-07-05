"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

ApiTokenMap = Mapping[str, tuple[str, frozenset[str]]]


def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean environment variable."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    """Read an integer environment variable."""
    raw = os.environ.get(name)
    return default if raw is None or raw.strip() == "" else int(raw)


def _env_float(name: str, default: float) -> float:
    """Read a float environment variable."""
    raw = os.environ.get(name)
    return default if raw is None or raw.strip() == "" else float(raw)


def _parse_api_tokens(raw: str | None) -> ApiTokenMap:
    """Parse ``DOCRAY_API_TOKENS`` into token -> (principal, scopes).

    Format: ``token=principal_uuid[:scope+scope]`` entries separated by comma or
    semicolon. Scopes default to ``read+write`` so one-token operator installs
    stay terse while still binding a real principal.
    """
    if raw is None or raw.strip() == "":
        return {}
    tokens: dict[str, tuple[str, frozenset[str]]] = {}
    for entry in raw.replace(";", ",").split(","):
        item = entry.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError("DOCRAY_API_TOKENS entries must use token=principal_id")
        token, principal_spec = item.split("=", 1)
        token = token.strip()
        principal_spec = principal_spec.strip()
        if not token or not principal_spec:
            raise ValueError("DOCRAY_API_TOKENS entries need non-empty token and principal")
        principal_id, _, scope_text = principal_spec.partition(":")
        scopes = (
            frozenset(scope.strip() for scope in scope_text.split("+") if scope.strip())
            if scope_text
            else frozenset({"read", "write"})
        )
        if not scopes:
            raise ValueError("DOCRAY_API_TOKENS scope list cannot be empty")
        tokens[token] = (principal_id.strip(), scopes)
    return tokens


@dataclass(frozen=True)
class Config:
    """Immutable runtime settings for the database and object store.

    Attributes:
        database_url: PostgreSQL connection URL.
        s3_endpoint: Base URL of the S3-compatible object store.
        s3_access_key: Access key for the object store.
        s3_secret_key: Secret key for the object store.
        s3_bucket: Name of the bucket holding original document bytes.
        s3_region: Region the object store reports itself in.
        api_pool_min_size: Minimum async API pool size.
        api_pool_max_size: Maximum async API pool size.
        api_pool_timeout: Seconds to wait for an API pool connection.
        api_statement_timeout_ms: Postgres ``statement_timeout`` applied to every
            pooled API connection, in milliseconds. Caps how long any single
            query can run so an unbounded read (e.g. a corpus-wide aggregation)
            fails fast instead of monopolizing a backend indefinitely.
        api_tokens: API bearer-token map to principal ids and scopes.
        api_allow_anonymous: Whether unauthenticated API requests get anonymous
            RLS-scoped read access instead of HTTP 401.
        backup_schedule_seconds: Seconds between scheduled backup enqueue attempts.
        backup_retention_count: Number of newest backup archives always retained.
        backup_retention_days: Age threshold for pruning archives outside the
            retained newest set.
    """

    database_url: str
    s3_endpoint: str
    s3_access_key: str
    s3_secret_key: str
    s3_bucket: str
    s3_region: str
    api_pool_min_size: int = 1
    api_pool_max_size: int = 10
    api_pool_timeout: float = 10.0
    api_statement_timeout_ms: int = 15_000
    api_tokens: ApiTokenMap = field(default_factory=dict)
    api_allow_anonymous: bool = True
    backup_schedule_seconds: int = 86_400
    backup_retention_count: int = 7
    backup_retention_days: int = 30

    @classmethod
    def from_env(cls) -> Config:
        """Build a ``Config`` from environment variables, applying defaults.

        Returns:
            A ``Config`` populated from ``DATABASE_URL`` and the ``S3_*``
            environment variables, falling back to local development defaults.
        """
        return cls(
            database_url=os.environ.get(
                "DATABASE_URL",
                "postgresql://docray:docray@localhost:5432/docray",
            ),
            s3_endpoint=os.environ.get("S3_ENDPOINT", "http://localhost:9000"),
            s3_access_key=os.environ.get("S3_ACCESS_KEY", "minioadmin"),
            s3_secret_key=os.environ.get("S3_SECRET_KEY", "minioadmin"),
            s3_bucket=os.environ.get("S3_BUCKET", "documents"),
            s3_region=os.environ.get("S3_REGION", "us-east-1"),
            api_pool_min_size=_env_int("DOCRAY_API_POOL_MIN_SIZE", 1),
            api_pool_max_size=_env_int("DOCRAY_API_POOL_MAX_SIZE", 10),
            api_pool_timeout=_env_float("DOCRAY_API_POOL_TIMEOUT", 10.0),
            api_statement_timeout_ms=_env_int("DOCRAY_API_STATEMENT_TIMEOUT_MS", 15_000),
            api_tokens=_parse_api_tokens(os.environ.get("DOCRAY_API_TOKENS")),
            api_allow_anonymous=_env_bool("DOCRAY_API_ALLOW_ANONYMOUS", True),
            backup_schedule_seconds=_env_int("DOCRAY_BACKUP_SCHEDULE_SECONDS", 86_400),
            backup_retention_count=_env_int("DOCRAY_BACKUP_RETENTION_COUNT", 7),
            backup_retention_days=_env_int("DOCRAY_BACKUP_RETENTION_DAYS", 30),
        )
