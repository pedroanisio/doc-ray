# Changelog

All notable changes to `doc-ray-graphql-maximizer` are recorded here.

The format follows Keep a Changelog conventions and the skill uses Semantic
Versioning through the repo-local `VERSION` file.

## [0.1.2] - 2026-07-05

### Fixed

- Added `list_documents.py` so common corpus inventory uses a deterministic
  helper with health checks, pagination, safe token loading, and table/JSON
  output instead of brittle ad hoc commands.
- Updated the skill and playbook to route "list all documents" through the
  bundled helper.

## [0.1.1] - 2026-07-05

### Fixed

- Added an auth/RLS visibility preflight so empty GraphQL corpus results are not
  misread as an empty database.
- Documented the webapp/API visibility difference: local webapp superuser
  passthrough can show documents that anonymous GraphQL cannot see.
- Extended the drift checker to validate API security, config, webapp RLS, and
  RBAC migration assumptions.

## [0.1.0] - 2026-07-05

### Added

- Initial Doc-Ray GraphQL maximizer skill.
- GraphQL value playbook covering discovery, document drill-down, annotation
  layers, lexeme/KWIC analysis, canonicalization, mutations, and job polling.
- `schema_digest.py` helper for live schema summaries.
- `install_or_update.py` installer/updater using SemVer comparison.
- `check_doc_ray_drift.py` drift checker for package metadata and Doc-Ray core
  GraphQL structure.
