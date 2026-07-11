"""Command-line auth administration for local doc-ray deployments."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from doc_ray.adapters.auth_admin_postgres import PostgresAuthAdminRepository, PrincipalRecord
from doc_ray.application.auth_admin import (
    ApiTokenEntry,
    append_api_token_entry,
    format_env_assignment,
    generate_api_token,
    parse_scope_text,
    read_env_value,
    upsert_env_value,
)
from doc_ray.config import Config


class AuthAdminRepository(Protocol):
    """Repository operations required by the auth admin CLI."""

    def resolve_principal(self, value: str) -> PrincipalRecord:
        """Resolve a principal by UUID or display name."""
        ...

    def create_principal(
        self, *, display_name: str, kind: str, external_subject: str | None = None
    ) -> PrincipalRecord:
        """Create a principal."""
        ...

    def assign_role(self, principal_id: str, role_name: str) -> None:
        """Assign a named role."""
        ...

    def grant_document(self, principal_id: str, document_id: str, permission: str) -> None:
        """Grant one document ACL permission."""
        ...

    def create_db_login(
        self, principal_id: str, role_name: str, password: str | None = None
    ) -> str:
        """Create a direct-SQL login role."""
        ...


RepositoryFactory = Callable[[str], AuthAdminRepository]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="doc_ray.entrypoints.auth_admin",
        description=(
            "Create doc-ray principals, API tokens, MCP client tokens, DB logins, and ACLs."
        ),
    )
    parser.add_argument("--database-url", help="Postgres URL; defaults to DATABASE_URL.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    principal = subcommands.add_parser("principal", help="Manage principals.")
    principal_commands = principal.add_subparsers(dest="principal_command", required=True)
    principal_create = principal_commands.add_parser(
        "create", help="Create or resolve a principal."
    )
    principal_create.add_argument("--display-name", required=True)
    principal_create.add_argument("--kind", choices=["human", "service"], default="human")
    principal_create.add_argument("--external-subject")
    principal_create.add_argument(
        "--role", choices=["reader", "curator", "admin"], action="append", default=[]
    )

    token = subcommands.add_parser("token", help="Manage API tokens.")
    token_commands = token.add_subparsers(dest="token_command", required=True)
    token_mint = token_commands.add_parser("mint", help="Mint a DOCRAY_API_TOKENS entry.")
    _add_token_mint_args(token_mint)

    mcp = subcommands.add_parser("mcp-token", help="Mint API tokens intended for MCP clients.")
    mcp_commands = mcp.add_subparsers(dest="mcp_command", required=True)
    mcp_mint = mcp_commands.add_parser("mint", help="Mint an MCP-client token entry.")
    _add_token_mint_args(mcp_mint)

    db_login = subcommands.add_parser("db-login", help="Manage direct database LOGIN roles.")
    db_login_commands = db_login.add_subparsers(dest="db_login_command", required=True)
    db_login_create = db_login_commands.add_parser("create", help="Create a principal DB login.")
    db_login_create.add_argument("--principal", required=True)
    db_login_create.add_argument("--role-name", required=True)
    password = db_login_create.add_mutually_exclusive_group()
    password.add_argument("--password")
    password.add_argument("--password-stdin", action="store_true")

    grant = subcommands.add_parser("grant", help="Grant document permissions.")
    grant_commands = grant.add_subparsers(dest="grant_command", required=True)
    grant_document = grant_commands.add_parser("document", help="Grant a document ACL.")
    grant_document.add_argument("--principal", required=True)
    grant_document.add_argument("--document-id", required=True)
    grant_document.add_argument(
        "--permission", choices=["read", "annotate", "delete", "redact", "admin"], required=True
    )

    return parser


def _add_token_mint_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--principal", required=True)
    parser.add_argument("--token", help="Use a caller-provided token instead of generating one.")
    parser.add_argument("--scopes", default="read+write", help="Scope list, e.g. read+write.")
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Append the token mapping to this .env file's DOCRAY_API_TOKENS value.",
    )
    parser.add_argument(
        "--fail-closed",
        action="store_true",
        help="Also set DOCRAY_API_ALLOW_ANONYMOUS=false when --env-file is used.",
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    repo_factory: RepositoryFactory | None = None,
) -> int:
    """Run the auth administration CLI."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    cfg = Config.from_env()
    database_url = args.database_url or cfg.database_url
    factory = repo_factory or PostgresAuthAdminRepository.from_database_url
    repo = factory(database_url)
    try:
        if args.command == "principal" and args.principal_command == "create":
            return _principal_create(args, repo)
        if args.command == "token" and args.token_command == "mint":
            return _token_mint(args, repo)
        if args.command == "mcp-token" and args.mcp_command == "mint":
            return _token_mint(args, repo)
        if args.command == "db-login" and args.db_login_command == "create":
            return _db_login_create(args, repo)
        if args.command == "grant" and args.grant_command == "document":
            return _grant_document(args, repo)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        close = getattr(repo, "close", None)
        if callable(close):
            close()
    parser.error("unhandled command")
    return 2


def _principal_create(args: argparse.Namespace, repo: AuthAdminRepository) -> int:
    principal = repo.create_principal(
        display_name=args.display_name,
        kind=args.kind,
        external_subject=args.external_subject,
    )
    for role_name in args.role:
        repo.assign_role(principal.id, role_name)
    role_text = ",".join(args.role) if args.role else "(none)"
    print(
        f"principal_id={principal.id} display_name={principal.display_name} "
        f"kind={principal.kind} roles={role_text}"
    )
    return 0


def _token_mint(args: argparse.Namespace, repo: AuthAdminRepository) -> int:
    principal = repo.resolve_principal(args.principal)
    entry = ApiTokenEntry(
        token=args.token or generate_api_token(),
        principal_id=principal.id,
        scopes=parse_scope_text(args.scopes),
    )
    assignment = format_env_assignment(entry)
    if args.env_file is not None:
        existing = read_env_value(args.env_file, "DOCRAY_API_TOKENS")
        merged = append_api_token_entry(existing, entry)
        upsert_env_value(args.env_file, "DOCRAY_API_TOKENS", merged)
        if args.fail_closed:
            upsert_env_value(args.env_file, "DOCRAY_API_ALLOW_ANONYMOUS", "false")
        print(f"token={entry.token}")
        print(f"principal_id={principal.id}")
        print(f"updated_env={args.env_file}")
        print("restart_required=true")
        return 0
    print(f"DOCRAY_API_TOKENS={assignment}")
    return 0


def _db_login_create(args: argparse.Namespace, repo: AuthAdminRepository) -> int:
    principal = repo.resolve_principal(args.principal)
    password = sys.stdin.read().strip() if args.password_stdin else args.password
    db_role = repo.create_db_login(principal.id, args.role_name, password)
    print(f"principal_id={principal.id} db_role={db_role}")
    return 0


def _grant_document(args: argparse.Namespace, repo: AuthAdminRepository) -> int:
    principal = repo.resolve_principal(args.principal)
    repo.grant_document(principal.id, args.document_id, args.permission)
    print(
        f"principal_id={principal.id} document_id={args.document_id} permission={args.permission}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
