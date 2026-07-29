# Changelog

All notable changes to `doc-ray-graphql-maximizer` are recorded here.

The format follows Keep a Changelog conventions and the skill uses Semantic
Versioning through the repo-local `VERSION` file.

## [0.3.0] - 2026-07-29

### Added

- Added an exhaustive, categorized inventory of all live Query and Mutation
  root fields, including evidence products, visual search/review, collections,
  lenses, sharing, operation schedules, billing, projections, and allusions.

### Changed

- The drift checker now fails closed when the playbook omits a live root field
  or retains a removed field. Schema growth can no longer pass as a warning.
- Expanded goal classification to route agents through the newer evidence,
  visual, organization, operations, billing, and relationship surfaces.
- Removed the unsupported custom frontmatter version key. Package version
  parity is now enforced across `VERSION`, README, CHANGELOG, and the SKILL body.
- Corrected mutation-return, pagination, and authorization guidance; clarified
  relationship semantics, visual-stack availability, and the distinction
  between checkout-generated schema and deployed-endpoint introspection.

## [0.2.1] - 2026-07-12

### Fixed

- `SKILL.md` frontmatter now carries a machine-readable `version:` field (the
  surface skill loaders actually read; previously the version lived only in
  the `VERSION` file, referenced from prose). `check_doc_ray_drift.py` fails
  hard when the frontmatter version and `VERSION` disagree, so installed
  copies can no longer mis-report themselves.

## [0.2.0] - 2026-07-12

### Added

- Documented the 2026-07-12 schema growth the drift check warned about:
  `auditEvents` and `documentMetadataSuggestions` queries; `cancelJob`,
  `reextractDocument`, `reextractDocuments(onlyMissingGeometry:)`,
  `archiveDocument`, `restoreDocument`, `bulkArchiveDocuments`,
  `bulkTagDocuments`, `suggestDocumentMetadata`, and
  `decideDocumentMetadataSuggestions(approveIds:, rejectIds:)` mutations —
  every signature verified against the live resolvers before documenting.
- Job monitoring guidance now covers the timing fields (`createdAt`,
  `startedAt`, `finishedAt`, `attempts`, `maxAttempts`), within-job progress
  (`progressCurrent`/`progressTotal`/`progressStage`), the stalled-corpse
  heuristic for `running` jobs with stale heartbeats, and the `/jobs/events`
  Server-Sent Events stream as the no-poll alternative.
- Lifecycle semantics: `deleteDocument` is a soft delete; archived/deleted
  documents drop out of default listings — added as a third "empty result"
  cause alongside RLS and tenant scope. `document.hasPositionData` documented
  as the overlay-readiness signal.

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
