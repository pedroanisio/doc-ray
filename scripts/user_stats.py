"""Report how many users are registered on a deployment, and how many are real.

Read-only. Every statement is a SELECT; the script never writes.

`principals` is NOT the answer to "how many users": it also holds service
accounts, the operator-seeded admin, and federated identities provisioned by
SCIM. The registered-user denominator is `principal_credentials` (migration
074) — one row per self-service signup — and the honest numerator is the subset
with `email_verified_at IS NOT NULL`. Both are reported, plus the gap between
them, because a large unverified fraction means the mail relay is failing, not
that the product is growing.

Emails in the recent-signups list are masked by default; `--emails` reveals
them.

Usage::

    python scripts/user_stats.py                  # text report, 10 recent signups
    python scripts/user_stats.py --json           # machine-readable
    python scripts/user_stats.py --limit 0        # totals only, no signup list
    python scripts/user_stats.py --emails         # unmasked addresses

DATABASE_URL is read from the environment, falling back to the repo's
``.env``/``.env.prod`` for any unset key.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import psycopg
from _env_fallback import load_env_fallback
from psycopg.rows import dict_row

DEFAULT_RECENT_LIMIT = 10

# One row per self-service signup. Activity keys off last_login_at (a login),
# never created_at — a signup that never returned is not an active user.
REGISTRATION_SQL = """
SELECT
    count(*)                                                             AS registered,
    count(*) FILTER (WHERE email_verified_at IS NOT NULL)                AS verified,
    count(*) FILTER (WHERE email_verified_at IS NULL)                    AS unverified,
    count(*) FILTER (WHERE disabled_at IS NOT NULL)                      AS disabled,
    count(*) FILTER (WHERE last_login_at IS NOT NULL)                    AS ever_logged_in,
    count(*) FILTER (WHERE last_login_at >= now() - interval '1 day')    AS active_1d,
    count(*) FILTER (WHERE last_login_at >= now() - interval '7 days')   AS active_7d,
    count(*) FILTER (WHERE last_login_at >= now() - interval '30 days')  AS active_30d,
    count(*) FILTER (WHERE created_at    >= now() - interval '1 day')    AS new_1d,
    count(*) FILTER (WHERE created_at    >= now() - interval '7 days')   AS new_7d,
    count(*) FILTER (WHERE created_at    >= now() - interval '30 days')  AS new_30d
FROM principal_credentials
"""

REGISTRATION_KEYS = (
    "registered",
    "verified",
    "unverified",
    "disabled",
    "ever_logged_in",
    "active_1d",
    "active_7d",
    "active_30d",
    "new_1d",
    "new_7d",
    "new_30d",
)

# The identity table behind the credentials: everything that can hold a role,
# including machines. `without_local_password` is the federated/SSO-only
# population — those principals never get a principal_credentials row.
PRINCIPAL_SQL = """
SELECT
    count(*)                                         AS principals,
    count(*) FILTER (WHERE kind = 'human')           AS humans,
    count(*) FILTER (WHERE kind = 'service')         AS services,
    count(*) FILTER (WHERE disabled_at IS NOT NULL)  AS disabled,
    count(*) FILTER (WHERE NOT active)               AS deactivated,
    count(*) FILTER (WHERE managed_by IS NOT NULL)   AS scim_managed,
    count(*) FILTER (
        WHERE kind = 'human'
          AND NOT EXISTS (
              SELECT 1 FROM principal_credentials c WHERE c.principal_id = p.id
          )
    )                                                AS without_local_password
FROM principals p
"""

PRINCIPAL_KEYS = (
    "principals",
    "humans",
    "services",
    "disabled",
    "deactivated",
    "scim_managed",
    "without_local_password",
)

# Rows are swept lazily, so an expired row may still be present; filtering on
# expires_at is what makes this "signed in right now" rather than "ever".
SESSION_SQL = """
SELECT count(*) AS sessions, count(DISTINCT principal_id) AS principals
FROM web_auth_sessions
WHERE expires_at > now()
"""

# LEFT JOIN on purpose: a tenant with zero signups is a fact worth seeing.
TENANT_SQL = """
SELECT t.slug,
       t.display_name,
       count(c.principal_id)                                            AS registered,
       count(c.principal_id) FILTER (WHERE c.email_verified_at IS NOT NULL) AS verified
FROM tenants t
LEFT JOIN principal_credentials c ON c.tenant_id = t.id
GROUP BY t.slug, t.display_name
ORDER BY registered DESC, t.slug
"""

RECENT_SQL = """
SELECT c.email_normalized,
       c.created_at,
       c.email_verified_at,
       c.last_login_at
FROM principal_credentials c
ORDER BY c.created_at DESC
LIMIT %s
"""


def _row_or_zeros(cursor: Any, keys: tuple[str, ...]) -> dict[str, int]:
    """Return the aggregate row as ints, or all-zeros on an empty deployment."""
    row = cursor.fetchone()
    if row is None:
        return dict.fromkeys(keys, 0)
    return {key: int(row.get(key) or 0) for key in keys}


def registration_stats(conn: Any) -> dict[str, int]:
    """Count self-service signups, verification state, and activity windows."""
    return _row_or_zeros(conn.execute(REGISTRATION_SQL), REGISTRATION_KEYS)


def principal_stats(conn: Any) -> dict[str, int]:
    """Count identities of every kind — humans, machines, and federated logins."""
    return _row_or_zeros(conn.execute(PRINCIPAL_SQL), PRINCIPAL_KEYS)


def session_stats(conn: Any) -> dict[str, int]:
    """Count unexpired webapp sessions and the distinct principals holding them."""
    return _row_or_zeros(conn.execute(SESSION_SQL), ("sessions", "principals"))


def tenant_breakdown(conn: Any) -> list[dict[str, Any]]:
    """Return per-tenant signup counts, including tenants with no users."""
    return [
        {
            "slug": row["slug"],
            "display_name": row["display_name"],
            "registered": int(row["registered"] or 0),
            "verified": int(row["verified"] or 0),
        }
        for row in conn.execute(TENANT_SQL).fetchall()
    ]


def recent_signups(conn: Any, limit: int) -> list[dict[str, Any]]:
    """Return the *limit* most recent signups, newest first."""
    return list(conn.execute(RECENT_SQL, (limit,)).fetchall())


def mask_email(email: str) -> str:
    """Hide the local part of *email*, keeping its first/last character and domain.

    The report is an operational metric, not a contact list; an operator reading
    it over a shoulder or pasting it into a ticket should not leak addresses.
    """
    local, sep, domain = (email or "").partition("@")
    if not sep or not local or not domain:
        return "***"
    if len(local) <= 2:
        return f"{local[0]}*@{domain}"
    return f"{local[0]}{'*' * (len(local) - 2)}{local[-1]}@{domain}"


def collect(conn: Any, *, limit: int, reveal_emails: bool) -> dict[str, Any]:
    """Gather the whole report in one pass over the connection."""
    report: dict[str, Any] = {
        "registrations": registration_stats(conn),
        "principals": principal_stats(conn),
        "sessions": session_stats(conn),
        "tenants": tenant_breakdown(conn),
        "recent": [],
    }
    if limit > 0:
        report["recent"] = [
            {
                "email": row["email_normalized"]
                if reveal_emails
                else mask_email(row.get("email_normalized") or ""),
                "created_at": row.get("created_at"),
                "verified_at": row.get("email_verified_at"),
                "last_login_at": row.get("last_login_at"),
            }
            for row in recent_signups(conn, limit)
        ]
    return report


def _pct(part: int, whole: int) -> str:
    return "—" if whole == 0 else f"{100.0 * part / whole:.0f}%"


def _stamp(value: Any) -> str:
    return "never" if value is None else str(value)[:19]


def render(report: dict[str, Any]) -> str:
    """Format the report as a text block for a terminal."""
    reg = report["registrations"]
    pri = report["principals"]
    ses = report["sessions"]
    lines = [
        "doc-ray — user registration",
        "=" * 52,
        f"Registered users (local signups) : {reg['registered']}",
        f"  email-verified                 : {reg['verified']} ({_pct(reg['verified'], reg['registered'])})",  # noqa: E501
        f"  unverified                     : {reg['unverified']}",
        f"  disabled                       : {reg['disabled']}",
        f"  ever logged in                 : {reg['ever_logged_in']}",
        "",
        f"Active (logged in)  1d / 7d / 30d : {reg['active_1d']} / {reg['active_7d']} / {reg['active_30d']}",  # noqa: E501
        f"New signups         1d / 7d / 30d : {reg['new_1d']} / {reg['new_7d']} / {reg['new_30d']}",
        f"Live webapp sessions              : {ses['sessions']} ({ses['principals']} principal(s))",
        "",
        "Identities (principals table)",
        "-" * 52,
        f"  human                          : {pri['humans']}",
        f"  service accounts               : {pri['services']}",
        f"  human, no local password (SSO) : {pri['without_local_password']}",
        f"  SCIM-managed                   : {pri['scim_managed']}",
        f"  disabled / deactivated         : {pri['disabled']} / {pri['deactivated']}",
    ]

    tenants = report["tenants"]
    populated = [t for t in tenants if t["registered"] > 0]
    # Test and SSO-provisioning residue leaves many empty tenants behind; listing
    # them buries the one that matters. Count them instead.
    if len(populated) > 1 or (populated and len(tenants) > 1):
        width = max(len(t["slug"]) for t in populated)
        lines += ["", "Per tenant", "-" * 52]
        lines += [
            f"  {t['slug']:<{width}}  {t['registered']:>5} registered, {t['verified']:>5} verified"
            for t in populated
        ]
        empty = len(tenants) - len(populated)
        if empty:
            lines.append(f"  (+{empty} tenant(s) with no signups)")

    if report["recent"]:
        width = max(len(str(r["email"])) for r in report["recent"])
        lines += ["", f"Most recent {len(report['recent'])} signup(s)", "-" * 52]
        lines += [
            f"  {r['email']:<{width}}  {_stamp(r['created_at'])}"
            f"  verified={_stamp(r['verified_at'])}"
            for r in report["recent"]
        ]
    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_RECENT_LIMIT,
        help=f"recent signups to list; 0 for totals only (default: {DEFAULT_RECENT_LIMIT})",
    )
    parser.add_argument(
        "--emails", action="store_true", help="show full addresses instead of masking them"
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a text report")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Connect, collect the report, and print it."""
    args = _parse_args(argv)

    loaded_from = load_env_fallback(("DATABASE_URL",))
    if loaded_from:
        print(f"(loaded DATABASE_URL from {loaded_from})", file=sys.stderr)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print(
            "fatal: DATABASE_URL is not set (and no .env/.env.prod supplied it).",
            file=sys.stderr,
        )
        return 2

    try:
        with psycopg.connect(database_url, row_factory=dict_row, autocommit=True) as conn:
            report = collect(conn, limit=max(0, args.limit), reveal_emails=args.emails)
    except psycopg.Error as exc:
        print(f"fatal: database error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, indent=2, default=str) if args.json else render(report))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
