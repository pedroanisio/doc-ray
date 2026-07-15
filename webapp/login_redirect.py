"""Post-login redirect: return a user to where they were headed after sign-in.

Used by the OAuth ``/authorize`` flow (which routes an unauthenticated user
through login and must resume the authorization request afterward) and by the
login handlers in :mod:`webapp.auth_web`. Extracted from ``auth_web`` (D-015) to
hold that module under the net-LOC cap. The stashed target is always validated
to be a *local* path to prevent open-redirect abuse.
"""

from __future__ import annotations

from flask import session, url_for

#: Flask-session key holding a local path to return to after login.
_POST_LOGIN_NEXT_KEY = "_post_login_next"


def _is_safe_local_path(target: object) -> bool:
    return isinstance(target, str) and target.startswith("/") and not target.startswith("//")


def stash_post_login_redirect(target: str) -> None:
    """Remember a *local* path to redirect to once the user has authenticated."""
    if _is_safe_local_path(target):
        session[_POST_LOGIN_NEXT_KEY] = target


def post_login_destination() -> str:
    """Return the stashed post-login path if safe, else the corpus home."""
    target = session.pop(_POST_LOGIN_NEXT_KEY, None)
    return target if _is_safe_local_path(target) else url_for("documents")
