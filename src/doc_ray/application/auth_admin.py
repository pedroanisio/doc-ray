"""Use cases for local auth administration."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path

# ``impersonate`` lets a trusted service token (the webapp) act on behalf of the
# logged-in browser user via ``X-DocRay-Act-As`` (see api/security._apply_act_as),
# so the API binds RLS to that USER, not the service principal. The runtime token
# parser (config.py) already honours it; the CLI must be able to mint it.
_VALID_SCOPES = frozenset({"read", "write", "impersonate"})


@dataclass(frozen=True)
class ApiTokenEntry:
    """One ``DOCRAY_API_TOKENS`` mapping entry."""

    token: str
    principal_id: str
    scopes: frozenset[str]


def parse_scope_text(scope_text: str) -> frozenset[str]:
    """Parse ``read+write`` style scope text."""
    scopes = frozenset(scope.strip() for scope in scope_text.split("+") if scope.strip())
    if not scopes:
        raise ValueError("scope list cannot be empty")
    unknown = scopes - _VALID_SCOPES
    if unknown:
        raise ValueError(f"unknown scope(s): {', '.join(sorted(unknown))}")
    return scopes


def generate_api_token(nbytes: int = 32) -> str:
    """Generate a URL-safe opaque API token."""
    while True:
        token = secrets.token_urlsafe(nbytes)
        if token and not any(ch in token for ch in "=,;"):
            return token


def format_env_assignment(entry: ApiTokenEntry) -> str:
    """Format one token mapping for ``DOCRAY_API_TOKENS``."""
    scopes = "+".join(sorted(entry.scopes))
    return f"{entry.token}={entry.principal_id}:{scopes}"


def append_api_token_entry(existing: str, entry: ApiTokenEntry) -> str:
    """Append *entry* to an existing ``DOCRAY_API_TOKENS`` value."""
    formatted = format_env_assignment(entry)
    existing = existing.strip()
    if not existing:
        return formatted
    for item in existing.replace(";", ",").split(","):
        token, _, _principal_spec = item.strip().partition("=")
        if token == entry.token:
            raise ValueError(f"token already exists in DOCRAY_API_TOKENS: {entry.token}")
    return f"{existing},{formatted}"


def read_env_value(path: Path, key: str) -> str:
    """Read an unquoted ``KEY=value`` assignment from *path* if present."""
    if not path.exists():
        return ""
    prefix = f"{key}="
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :]
    return ""


def upsert_env_value(path: Path, key: str, value: str) -> None:
    """Insert or replace an unquoted ``KEY=value`` assignment in *path*."""
    prefix = f"{key}="
    replacement = f"{prefix}{value}\n"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True) if path.exists() else []
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = replacement
            break
    else:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] = f"{lines[-1]}\n"
        lines.append(replacement)
    path.write_text("".join(lines), encoding="utf-8")
