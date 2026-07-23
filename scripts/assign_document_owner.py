"""Assign document ownership to a principal, by login email (migration 102).

Migration 102 made ``documents.owner_principal_id`` the read boundary: a
document is readable by its owner, an explicit ACL grantee, or a tenant admin.
Documents ingested before 102 — and anything the operator CLI ingests — carry
``owner_principal_id IS NULL``, which is *unowned*: admin-only, and reserved
for the org corpus. This moves such documents into a real user's corpus.

Deployment-specific data does not belong in a versioned migration, so the
schema change and the data move are separate on purpose: 102 ships the column
and the policies, this script decides who owns what on a given deployment.

Dry-run by default — it prints what it would change and exits without writing.
Pass ``--apply`` to commit. Both modes are idempotent.

Usage::

    python scripts/assign_document_owner.py --email someone@example.com --unowned
    python scripts/assign_document_owner.py --email someone@example.com --unowned --apply
    python scripts/assign_document_owner.py --email someone@example.com \\
        --from-email operator@example.com --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

import psycopg
from psycopg.rows import dict_row


def resolve_principal(conn: Any, email: str) -> str | None:
    """Return the principal id owning *email*'s local credential, or ``None``.

    Matches ``principal_credentials.email_normalized`` — the login identity —
    rather than ``principals.display_name``, which is a free-text label and is
    neither unique nor an identifier.
    """
    row = conn.execute(
        "SELECT principal_id FROM principal_credentials WHERE email_normalized = %s",
        (email.strip().lower(),),
    ).fetchone()
    return None if row is None else str(row["principal_id"])


def select_documents(conn: Any, *, unowned: bool, from_principal: str | None) -> list[str]:
    """Return the ids of the documents the requested selector matches."""
    if from_principal is not None:
        rows = conn.execute(
            "SELECT id FROM documents WHERE owner_principal_id = %s ORDER BY created_at",
            (from_principal,),
        ).fetchall()
    elif unowned:
        rows = conn.execute(
            "SELECT id FROM documents WHERE owner_principal_id IS NULL ORDER BY created_at"
        ).fetchall()
    else:  # pragma: no cover - argparse enforces one selector
        rows = []
    return [str(row["id"]) for row in rows]


def assign(conn: Any, document_ids: list[str], owner: str) -> int:
    """Point *document_ids* at *owner*; returns the number of rows changed."""
    if not document_ids:
        return 0
    cur = conn.execute(
        "UPDATE documents SET owner_principal_id = %s, updated_at = now() WHERE id = ANY(%s)",
        (owner, document_ids),
    )
    return int(cur.rowcount)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--email", required=True, help="login email of the receiving principal")
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument(
        "--unowned", action="store_true", help="move every document with no owner"
    )
    selector.add_argument(
        "--from-email", help="move every document currently owned by this principal"
    )
    parser.add_argument("--apply", action="store_true", help="commit the change (default: dry run)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Resolve the principals, report the move, and apply it when asked."""
    args = _parse_args(argv)
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("fatal: DATABASE_URL is not set", file=sys.stderr)
        return 2

    with psycopg.connect(database_url, row_factory=dict_row, autocommit=True) as conn:
        owner = resolve_principal(conn, args.email)
        if owner is None:
            print(f"fatal: no principal with login email {args.email!r}", file=sys.stderr)
            return 1
        from_principal = None
        if args.from_email:
            from_principal = resolve_principal(conn, args.from_email)
            if from_principal is None:
                print(f"fatal: no principal with login email {args.from_email!r}", file=sys.stderr)
                return 1

        document_ids = select_documents(conn, unowned=args.unowned, from_principal=from_principal)
        source = f"owned by {args.from_email}" if args.from_email else "unowned"
        print(f"{len(document_ids)} document(s) {source} -> {args.email} ({owner})")
        if not args.apply:
            print("dry run — pass --apply to commit")
            return 0
        changed = assign(conn, document_ids, owner)
        print(f"assigned {changed} document(s)")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
