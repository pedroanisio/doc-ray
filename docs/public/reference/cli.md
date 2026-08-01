---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Operator CLI reference"
doc_type: "reference"
audience: ["operator", "administrator"]
surfaces: ["cli"]
classification: "public"
applies_to: "1.57.3"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Operator CLI reference

This page is the contract for the supported Operator CLI commands: invocation, purpose, prerequisites, and expected output. Each command is run in the deployment environment as a Python module.

Every command reads its database connection from `DATABASE_URL` unless a command-specific override is provided.

## `auth_admin` — provision principals and tokens

Provisions tenants, principals, and API tokens, and grants Document permissions.

```
python -m doc_ray.entrypoints.auth_admin [--database-url URL] <command> ...
```

Commands:

- `tenant create --slug <slug> --display-name <name>` — create or update a tenant.
- `principal create --display-name <name> [--kind human|service] [--external-subject <id>] [--tenant <uuid-or-slug>]` — create or resolve a principal.
- `token mint --principal <uuid> [--token <value>] [--scopes read+write] [--env-file <path>]` — mint a `DOCRAY_API_TOKENS` entry for API access.
- `mcp-token mint --principal <uuid> [--token <value>] [--scopes read+write] [--env-file <path>]` — mint a token intended for MCP clients.
- `db-login create --principal <uuid> --role-name <name> [--password <value> | --password-stdin]` — create a direct database login role.
- `grant document --principal <uuid> --document-id <uuid> --permission read|annotate|delete|redact|admin` — grant a Document permission.

Prerequisites: reachable database; sufficient privilege to provision principals.

Expected output: the created or resolved identifier, and for the mint commands, a `DOCRAY_API_TOKENS` entry line (written to the `--env-file` when supplied). Treat every minted token as a secret and redact it from logs and examples.

## `migrate` — apply schema migrations

Applies pending database migrations.

```
python -m doc_ray.entrypoints.migrate [--dry-run]
```

- `--dry-run` — list the migrations that would be applied, then exit without applying them.

Prerequisites: reachable database with privilege to alter schema.

Expected output: the migrations applied (or, with `--dry-run`, the migrations that would be applied). Run this before starting a new version.

Exit codes: `0` on success (migrations applied, or nothing pending); `2` when the database is unreachable; `130`/`143` when interrupted by SIGINT/SIGTERM (`128 + signal`). On a stop signal the runner **cancels the in-flight statement and exits promptly and cleanly** instead of being force-killed (exit 137) after the container's grace window — so a routine `docker compose down` during a long backfill no longer produces an alarming hard-kill. Every migration is idempotent and self-recording, so a canceled run simply resumes on the next start. In Compose, `postgres` and `migrate` set an explicit `stop_grace_period` so Postgres can finish its shutdown checkpoint and the runner can cancel and unwind before Docker escalates to SIGKILL.

## `backup_scheduler` — scheduled backups

Enqueues Backup Jobs on a cadence and prunes expired archives.

```
python -m doc_ray.entrypoints.backup_scheduler [--once] [--interval SECONDS] [--keep-latest N] [--retention-days DAYS]
```

- `--once` — run one enqueue-and-prune pass, then exit; omit to run continuously.
- `--interval` — seconds between enqueue attempts. Defaults to `DOCRAY_BACKUP_SCHEDULE_SECONDS`.
- `--keep-latest` — newest archives to keep regardless of age. Defaults to `DOCRAY_BACKUP_RETENTION_COUNT`.
- `--retention-days` — prune archives older than this once they fall outside the kept newest. Defaults to `DOCRAY_BACKUP_RETENTION_DAYS`.

Prerequisites: reachable database and configured object storage.

Expected output: a log of each enqueue and prune pass. A single Backup is enqueued only when no Backup is already active. See [Configuration](./config.md) and [Backups](../concepts/backups.md).

## `corpus_health` — corpus diagnostics

Reports the health of the Corpus.

```
python -m doc_ray.entrypoints.corpus_health [--json]
```

- `--json` — emit the report as JSON instead of text.

Prerequisites: reachable database.

Expected output: a health snapshot with any detected issues. When the database is unreachable, the command reports the failure on standard error and exits non-zero.

## `db_health` — database diagnostics

Reports the health of the database instance.

```
python -m doc_ray.entrypoints.db_health [--json] [--watch SECONDS]
```

- `--json` — emit the report as JSON instead of text.
- `--watch` — repeat the report every N seconds (must be greater than zero).

Prerequisites: reachable database.

Expected output: a health snapshot with any detected issues. When the database is unreachable, the command reports the failure on standard error and exits non-zero.

## `db_analyze` — database structure and data profile

Inspects database structure and profiles its data, writing Markdown and JSON reports.

```
python -m doc_ray.entrypoints.db_analyze [--json] [--structure-only] [--schemas LIST] [--exclude-schemas LIST] [--output-dir DIR] [--dsn DSN]
```

- `--json` — print the JSON report instead of Markdown.
- `--structure-only` — skip data profiling.
- `--schemas` / `--exclude-schemas` — comma-separated schemas to include or skip.
- `--output-dir` — directory the reports are written to.
- `--dsn` — connection string override.

The command runs under a read-only guard by default. Prerequisites: reachable database.

Expected output: the report on standard output and `db_analysis.md` / `db_analysis.json` written to the output directory (its path is reported on standard error).

## `cli` — batch document ingestion

Loads one or more documents into the store from local files or directories.

```
python -m doc_ray.entrypoints.cli <file-or-directory> [<file-or-directory> ...]
```

- Directories are searched recursively. Unsupported file types are skipped.

Prerequisites: reachable database and configured object storage.

Expected output: a progress line per file and a final summary. Exit codes: `0` on success, `1` when no supported documents are found, `2` when any file fails. For ingestion through the API instead, see [GraphQL API](./api.md).

## Related concepts, how-tos, and reference

- Concept: [Access and roles](../concepts/access-and-roles.md)
- Concept: [Backups](../concepts/backups.md)
- Concept: [Corpus and documents](../concepts/corpus-and-documents.md)
- How-to: [Obtain an API token](../how-to/obtain-an-api-token.md)
- Reference: [Configuration](./config.md)
- Reference: [GraphQL API](./api.md)
</content>
