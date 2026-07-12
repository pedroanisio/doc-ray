"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

ApiTokenMap = Mapping[str, tuple[str, frozenset[str]]]
SCIMTokenMap = Mapping[str, str]

#: The one host-side fallback when DATABASE_URL is unset (BL-127). Matches the
#: compose factory default (POSTGRES_PORT 5432); local overrides — a remapped
#: port, a real password — belong in .env, never in per-module literals.
DEFAULT_DATABASE_URL = "postgresql://docray:docray@localhost:5432/docray"


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


def _env_optional_str(name: str) -> str | None:
    """Read a non-empty string environment variable."""
    raw = os.environ.get(name)
    return None if raw is None or raw.strip() == "" else raw.strip()


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


def _parse_scim_tokens(raw: str | None) -> SCIMTokenMap:
    """Parse ``DOCRAY_SCIM_TOKENS`` into token -> tenant UUID mappings."""
    if raw is None or raw.strip() == "":
        return {}
    tokens: dict[str, str] = {}
    for entry in raw.replace(";", ",").split(","):
        item = entry.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError("DOCRAY_SCIM_TOKENS entries must use token=tenant_id")
        token, tenant_id = item.split("=", 1)
        token = token.strip()
        tenant_id = tenant_id.strip()
        if not token or not tenant_id:
            raise ValueError("DOCRAY_SCIM_TOKENS entries need non-empty token and tenant")
        tokens[token] = tenant_id
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
        s3_allow_insecure_endpoint: Whether a plaintext ``http://`` object-store
            endpoint is an explicitly accepted local-development configuration.
        s3_server_side_encryption: Optional S3 SSE algorithm to request on writes,
            e.g. ``AES256`` or ``aws:kms``.
        s3_sse_kms_key_id: Optional KMS key id/alias sent when using KMS-backed SSE.
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
        api_jwt_secret: Optional HMAC secret used to issue and verify short-lived
            API access JWTs. Empty disables JWT support while keeping static
            bearer tokens available.
        api_jwt_issuer: Expected ``iss`` claim for locally issued access JWTs.
        api_jwt_audience: Optional expected ``aud`` claim for locally issued
            access JWTs.
        api_jwt_ttl_seconds: Default lifetime for issued access JWTs.
        api_refresh_token_ttl_seconds: Default lifetime for refresh tokens used
            to rotate API access credentials.
        api_oidc_issuer: Optional expected OIDC issuer. When set with
            ``api_oidc_jwks_json``, OIDC bearer JWTs become the default API
            authenticator and static tokens remain only as fallback credentials.
        api_oidc_audience: Optional required OIDC audience.
        api_oidc_jwks_json: JWKS document JSON used to verify OIDC bearer JWTs.
        api_oidc_auto_create_principals: Whether OIDC login may create a
            ``principals`` row for a previously unseen ``sub`` claim. This is
            the local policy switch BL-066 needs before BL-068 adds full SCIM
            authority.
        api_oidc_allow_static_token_fallback: Whether static bearer tokens
            remain accepted when OIDC is configured. Defaults false so the
            weaker local/break-glass path is explicit instead of implicit.
        api_debug: Whether GraphQL introspection and the GraphiQL IDE are
            enabled (BL-108). False in production by default: an anonymous
            caller should not be able to enumerate the schema, including
            admin-only mutation shapes, for free reconnaissance.
        scim_tokens: SCIM bearer-token map to tenant ids. Each token is scoped
            to exactly one tenant's ``/scim/v2/{tenant_id}`` endpoint so a
            tenant IdP cannot provision principals in another tenant.
        max_upload_bytes: Maximum accepted upload size in bytes, shared by the
            webapp's Flask route and the GraphQL ``ingestDocument`` mutation
            (BL-098) so both upload paths enforce the same ceiling.
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
    s3_allow_insecure_endpoint: bool = False
    s3_server_side_encryption: str | None = None
    s3_sse_kms_key_id: str | None = None
    api_pool_min_size: int = 1
    api_pool_max_size: int = 10
    api_pool_timeout: float = 10.0
    api_statement_timeout_ms: int = 15_000
    api_tokens: ApiTokenMap = field(default_factory=dict)
    api_allow_anonymous: bool = True
    api_jwt_secret: str | None = None
    api_jwt_issuer: str = "doc-ray"
    api_jwt_audience: str | None = None
    api_jwt_ttl_seconds: int = 900
    api_refresh_token_ttl_seconds: int = 86_400
    api_oidc_issuer: str | None = None
    api_oidc_audience: str | None = None
    api_oidc_jwks_json: str | None = None
    api_oidc_auto_create_principals: bool = False
    api_oidc_allow_static_token_fallback: bool = False
    api_debug: bool = False
    scim_tokens: SCIMTokenMap = field(default_factory=dict)
    max_upload_bytes: int = 200 * 1024 * 1024
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
            database_url=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
            s3_endpoint=os.environ.get("S3_ENDPOINT", "http://localhost:9000"),
            s3_access_key=os.environ.get("S3_ACCESS_KEY", "minioadmin"),
            s3_secret_key=os.environ.get("S3_SECRET_KEY", "minioadmin"),
            s3_bucket=os.environ.get("S3_BUCKET", "documents"),
            s3_region=os.environ.get("S3_REGION", "us-east-1"),
            s3_allow_insecure_endpoint=_env_bool("S3_ALLOW_INSECURE_ENDPOINT", False),
            s3_server_side_encryption=_env_optional_str("S3_SERVER_SIDE_ENCRYPTION"),
            s3_sse_kms_key_id=_env_optional_str("S3_SSE_KMS_KEY_ID"),
            api_pool_min_size=_env_int("DOCRAY_API_POOL_MIN_SIZE", 1),
            api_pool_max_size=_env_int("DOCRAY_API_POOL_MAX_SIZE", 10),
            api_pool_timeout=_env_float("DOCRAY_API_POOL_TIMEOUT", 10.0),
            api_statement_timeout_ms=_env_int("DOCRAY_API_STATEMENT_TIMEOUT_MS", 15_000),
            api_tokens=_parse_api_tokens(os.environ.get("DOCRAY_API_TOKENS")),
            api_allow_anonymous=_env_bool("DOCRAY_API_ALLOW_ANONYMOUS", True),
            api_jwt_secret=_env_optional_str("DOCRAY_API_JWT_SECRET"),
            api_jwt_issuer=os.environ.get("DOCRAY_API_JWT_ISSUER", "doc-ray").strip() or "doc-ray",
            api_jwt_audience=_env_optional_str("DOCRAY_API_JWT_AUDIENCE"),
            api_jwt_ttl_seconds=_env_int("DOCRAY_API_JWT_TTL_SECONDS", 900),
            api_refresh_token_ttl_seconds=_env_int("DOCRAY_API_REFRESH_TOKEN_TTL_SECONDS", 86_400),
            api_oidc_issuer=_env_optional_str("DOCRAY_API_OIDC_ISSUER"),
            api_oidc_audience=_env_optional_str("DOCRAY_API_OIDC_AUDIENCE"),
            api_oidc_jwks_json=_env_optional_str("DOCRAY_API_OIDC_JWKS_JSON"),
            api_oidc_auto_create_principals=_env_bool(
                "DOCRAY_API_OIDC_AUTO_CREATE_PRINCIPALS", False
            ),
            api_oidc_allow_static_token_fallback=_env_bool(
                "DOCRAY_API_OIDC_ALLOW_STATIC_TOKEN_FALLBACK", False
            ),
            api_debug=_env_bool("DOCRAY_API_DEBUG", False),
            scim_tokens=_parse_scim_tokens(os.environ.get("DOCRAY_SCIM_TOKENS")),
            max_upload_bytes=_env_int("DOCRAY_MAX_UPLOAD_MB", 200) * 1024 * 1024,
            backup_schedule_seconds=_env_int("DOCRAY_BACKUP_SCHEDULE_SECONDS", 86_400),
            backup_retention_count=_env_int("DOCRAY_BACKUP_RETENTION_COUNT", 7),
            backup_retention_days=_env_int("DOCRAY_BACKUP_RETENTION_DAYS", 30),
        )
