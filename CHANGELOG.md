---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Fable 5 via Claude Code"
  date: "2026-07-02"
---

# Changelog

All notable changes to doc-ray are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

doc-ray carries **one global version** covering both the application package
(`doc_ray.__version__`) and the database schema contract
(`docray_schema_version()`). They are kept in lockstep by
`scripts/check_version_consistency.py`, which fails the build on any drift.

## [Unreleased]

Delivered on this branch, not yet released. Cutting the release (and choosing the
SemVer bump) is an operator decision: `backups` is an operational table and does
not change `docray_schema_version()`, so existing `1.3.0` archives stay restorable.

### Added
- **Backup & restore** — full-corpus snapshot and point-in-time restore, driven
  from the webapp (`/backups`) and the GraphQL API. A backup bundles a logical
  database dump (every corpus table via binary `COPY`, preserving `tsvector`,
  `vector(768)`, `jsonb`, and array columns exactly) together with every stored
  object-store blob into a single portable `.tar.gz` archive.
  - `db/migrations/026_backups.sql`: the `backups` catalog table — status
    lifecycle `creating → available | failed | restoring`, archive/table/blob/doc
    counts, and the `schema_version` captured at backup time.
  - GraphQL mutations `createBackup(note)` and `restoreBackup(backupId)`, each
    enqueuing background work through the single write path.
  - Worker job kinds `backup` and `restore`. The engine (`application/backup.py`)
    dumps under `REPEATABLE READ` and restores under
    `session_replication_role = replica`, so foreign keys and the FTS trigger
    defer and the dumped tsvectors are preserved verbatim. The live `jobs` queue
    and the `backups` catalog are excluded from both dump and restore, so a
    restore never clobbers the queue or the index it is reading from.
  - `ObjectStore.list_objects(prefix)` on the `ObjectStore` port and its MinIO/S3
    adapter, letting the engine enumerate blobs to bundle.
  - Webapp `/backups` screen: list archives, create (optional note), download, and
    restore (guarded by a destructive-action confirmation).
  - Archive downloads are served by the API (`GET /backups/{id}/download`) and
    proxied by the webapp, so the browser tier never needs direct object-store
    access — a host-vs-container `S3_ENDPOINT` mismatch cannot break downloads.

## [1.3.0] - 2026-07-02

Adoption of a single, enforced SemVer line. This release reconciles the packaging
version (previously a static `0.1.0`) and the database schema version (previously
`1.0.0`) into one global `1.3.0`, and adds the machinery that keeps every version
surface consistent from here on.

### Added
- Single source of truth for the project version: `doc_ray.__version__`.
  `pyproject.toml` derives its version from this attribute rather than hard-coding it.
- `scripts/check_version_consistency.py`: a stdlib-only, offline gate asserting that
  the engineering-decisions record, this changelog, and the latest schema migration
  all agree with `doc_ray.__version__`. Wired into pre-commit.
- GraphQL `version` query field and a `version` field on the `/health` response, so
  the running service reports its semantic version.
- `db/migrations/024_schema_version_1_3_0.sql`: advances the database schema version
  to `1.3.0`.

### Changed
- Database schema version `1.0.0` → `1.3.0`, reconciling three backward-compatible
  additions that had landed without a version bump (SemVer drift):
  migration 021 (canonical entity registry), 022 (form index), 023 (resolution store).
- `engineering_decisions.yml` `source_schema_version` `1.0.0` → `1.3.0`.

### Fixed
- GraphQL `ingestDocument` multipart uploads were rejected at the transport layer
  (Strawberry `multipart_uploads_enabled` defaults to `False`); the upload mutation
  is now reachable over HTTP.
- Upload ingestion no longer leaks the temporary scratch filename into a document's
  `title` / `original_filename`; the uploaded name and explicit `title` are preserved.

[1.3.0]: https://semver.org/spec/v2.0.0.html
