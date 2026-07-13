"""X-DocRay-Act-As delegation tests (BL-071 authorization-inversion MAJOR).

Split out of test_api_jwt.py (D-015): a service token carrying the explicit
'impersonate' scope may act on behalf of a user principal, so RLS applies to
the user rather than the service token's admin identity.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from fastapi import HTTPException

from doc_ray.entrypoints.api.security import HeaderTokenAuthenticator


class _Request:
    def __init__(self, token: str, act_as: str | None = None) -> None:
        self.headers = {"authorization": f"Bearer {token}"}
        if act_as is not None:
            self.headers["x-docray-act-as"] = act_as


def test_act_as_header_delegates_principal_with_impersonate_scope():
    """Regression for the authorization-inversion MAJOR: a service token with
    the explicit 'impersonate' scope may act on behalf of a user principal —
    RLS then applies to the USER, not the service token's admin principal."""
    service_principal, user_principal = str(uuid4()), str(uuid4())
    authenticator = HeaderTokenAuthenticator(
        tokens={"svc-token": (service_principal, frozenset({"read", "write", "impersonate"}))},
        allow_anonymous=False,
    )

    async def check() -> None:
        principal = await authenticator.authenticate(  # type: ignore[arg-type]
            _Request("svc-token", act_as=user_principal)
        )
        assert principal.principal_id == user_principal
        # Both identities stay visible for the audit trail.
        assert "svc-token" in (principal.subject or "")
        assert user_principal in (principal.subject or "")
        # The delegation capability itself must not be forwarded.
        assert "impersonate" not in principal.scopes

    asyncio.run(check())


def test_act_as_header_is_rejected_without_impersonate_scope():
    authenticator = HeaderTokenAuthenticator(
        tokens={"svc-token": (str(uuid4()), frozenset({"read", "write"}))},
        allow_anonymous=False,
    )

    async def check() -> None:
        with pytest.raises(HTTPException) as exc:
            await authenticator.authenticate(  # type: ignore[arg-type]
                _Request("svc-token", act_as=str(uuid4()))
            )
        assert exc.value.status_code == 403

    asyncio.run(check())


def test_act_as_header_rejects_malformed_principal_id():
    authenticator = HeaderTokenAuthenticator(
        tokens={"svc-token": (str(uuid4()), frozenset({"read", "impersonate"}))},
        allow_anonymous=False,
    )

    async def check() -> None:
        with pytest.raises(HTTPException) as exc:
            await authenticator.authenticate(  # type: ignore[arg-type]
                _Request("svc-token", act_as="not-a-uuid")
            )
        assert exc.value.status_code == 400

    asyncio.run(check())
