"""Browser-auth provider/config predicates read from the environment.

Extracted from :mod:`webapp.auth_web` (D-015). These are pure ``os.environ``
readers with no dependency on the rest of the auth module, so they group
cleanly here and keep ``auth_web`` under the net-LOC cap.
"""

from __future__ import annotations

import os


def env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean environment flag with the shared truthy vocabulary."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def local_auth_enabled() -> bool:
    """Return whether local email/password login is enabled."""
    return env_bool("DOCRAY_AUTH_LOCAL_ENABLED", False)


def local_registration_enabled() -> bool:
    """Return whether self-service registration is enabled."""
    return env_bool("DOCRAY_AUTH_REGISTRATION_ENABLED", False)


def oidc_auth_enabled() -> bool:
    """Return whether an OIDC authorization endpoint is configured."""
    return bool(os.environ.get("DOCRAY_WEBAPP_OIDC_AUTHORIZATION_URL", "").strip())


def saml_auth_enabled() -> bool:
    """Return whether a SAML SSO endpoint is configured."""
    return bool(os.environ.get("DOCRAY_WEBAPP_SAML_SSO_URL", "").strip())


def web_auth_required() -> bool:
    """Return whether any browser auth provider is configured or forced on."""
    return (
        env_bool("DOCRAY_WEBAPP_REQUIRE_AUTH", False)
        or local_auth_enabled()
        or oidc_auth_enabled()
        or saml_auth_enabled()
    )
