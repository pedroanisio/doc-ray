Iterate autonomously on this codebase until every applicable requirement below is demonstrably satisfied.

Do not optimize for minimal changes. Refactor, reorganize, replace, or remove existing implementation when necessary to reach the target architecture and quality bar.

Treat these requirements as acceptance criteria, not suggestions.

## 1. Execution Rules

- Inspect and understand the existing codebase before making architectural decisions.
- Preserve existing valid behavior unless a requirement explicitly demands changing it.
- Continuously run relevant tests, static analysis, linters, type checks, security checks, migrations, and integration tests while iterating.
- Do not claim a requirement is complete without executable or inspectable evidence.
- Do not bypass failing tests, weaken assertions, exclude meaningful files from coverage, silence errors, or introduce mocks merely to make metrics pass.
- Prefer root-cause fixes over patches.
- Remove dead code, duplicated abstractions, obsolete compatibility layers, and unused dependencies when safe.
- Keep the repository in a runnable state after each major iteration.
- When requirements conflict, prioritize:
  1. correctness
  2. security and data integrity
  3. domain correctness
  4. architectural integrity
  5. testability
  6. operability
  7. maintainability
  8. developer experience

## 2. Docker / Production Deployment

The entire application must be fully containerized and production-ready for deployment on DigitalOcean.

Required:

- Dockerfiles for every deployable component.
- Multi-stage production builds where appropriate.
- Non-root containers.
- Health checks.
- Explicit configuration through environment variables.
- `.env.example` containing every supported configuration variable without secrets.
- Reproducible dependency installation.
- Persistent services configured correctly.
- Database migrations executed safely.
- Startup and shutdown behavior suitable for production.
- Docker Compose environment capable of running the complete stack locally.
- Production deployment documentation for DigitalOcean.
- Production and local configurations must remain behaviorally aligned.

The complete system must be startable from a clean machine with a single documented bootstrap command or script.

Example:

    ./install.sh

The script must validate prerequisites, configure the environment, build the services, initialize required infrastructure, run migrations, seed required data, and verify application health.

## 3. Architecture

Refactor the system toward rigorous Domain-Driven Design and Clean Architecture.

Required conceptual boundaries:

- Domain
- Application / Use Cases
- Ports / Interfaces
- Infrastructure / Adapters
- API / Delivery

Business logic must not depend directly on:

- FastAPI/Flask/Django/etc.
- GraphQL frameworks
- PostgreSQL drivers or ORMs
- MinIO/S3 SDKs
- HTTP clients
- payment providers
- external AI providers
- infrastructure-specific libraries

Dependency direction must point inward toward the domain.

Use DDD concepts where genuinely applicable:

- Entities
- Value Objects
- Aggregates
- Aggregate Roots
- Domain Services
- Domain Events
- Repositories
- Application Services / Use Cases
- Bounded Contexts
- Explicit domain invariants

Do not introduce ceremonial abstractions with no architectural value.

## 4. Code Size

No source-code file may exceed 500 net LOC.

Net LOC excludes:

- blank lines
- comment-only lines

Generated files, migrations, vendored dependencies, lock files, and machine-generated schemas may be exempt when splitting them would provide no engineering value.

If a handwritten source file exceeds the limit, decompose it based on responsibility rather than mechanically splitting it.

## 5. Engineering Principles

The resulting codebase must strongly follow:

- SOLID
- DRY
- explicit dependency inversion
- separation of concerns
- high cohesion
- low coupling
- composition over inheritance where appropriate
- explicit interfaces at architectural boundaries
- deterministic domain behavior where possible

Avoid:

- god objects
- god modules
- generic `utils` dumping grounds
- duplicated business rules
- hidden global state
- implicit dependencies
- circular dependencies
- framework-driven domain models
- unnecessary abstraction layers

## 6. PostgreSQL

PostgreSQL must be the persistence backend in:

- development
- automated tests
- UAT
- production

Do not substitute SQLite or another database for tests.

Required:

- migrations
- indexes
- constraints
- foreign keys
- transaction boundaries
- concurrency-safe behavior where relevant
- connection pooling
- appropriate isolation semantics
- migration rollback/recovery strategy
- automated integration tests against real PostgreSQL

Database constraints should enforce domain invariants whenever appropriate.

## 7. Testing

Achieve at least:

- 90% line coverage
- 90% branch coverage where supported and meaningful

Coverage must include production application code and must not be artificially inflated through exclusions.

Implement:

- unit tests
- domain tests
- application/use-case tests
- repository integration tests
- PostgreSQL integration tests
- REST API integration tests
- GraphQL integration tests
- MinIO/S3 integration tests
- authentication/authorization tests
- tenancy-isolation tests
- billing-related tests
- regression tests
- happy paths
- edge cases
- error paths
- end-to-end tests

The E2E suite must exercise the real application stack using real PostgreSQL and real MinIO-compatible storage.

Mock only external systems where running the real dependency is unreasonable.

## 8. API Surface

Expose a modern API through both:

- REST
- GraphQL

Both interfaces must use the same application/domain logic rather than duplicating business rules.

REST requirements:

- consistent resource modeling
- proper HTTP semantics
- structured errors
- validation
- pagination
- filtering/sorting where relevant
- API versioning strategy
- generated OpenAPI specification

GraphQL requirements:

- typed schema
- structured domain errors
- pagination
- authorization
- protections against pathological queries
- documented queries, mutations, and relevant subscriptions if applicable

Maintain behavioral parity between REST and GraphQL whenever both expose the same capability.

## 9. Fixtures and Seed Data

Provide:

- deterministic fixtures
- development seed data
- UAT seed data where appropriate
- test factories/builders
- idempotent seed mechanisms

Never seed production with unsafe demo/test credentials or data.

## 10. MinIO / S3

Use MinIO as the local and test S3-compatible object-storage implementation.

Storage access must be abstracted behind an application port so production can use any compatible S3 backend without changing domain logic.

Cover:

- upload
- download
- deletion
- metadata
- failure handling
- authorization
- object naming
- cleanup
- test isolation

## 11. PII Readiness

Design the platform to safely process Personally Identifiable Information.

At minimum implement or document:

- classification of sensitive data
- encryption in transit
- encryption at rest where applicable
- secret management
- least-privilege access
- authorization boundaries
- tenant isolation
- sensitive-field redaction from logs
- audit trails
- retention strategy
- deletion/anonymization mechanisms
- export mechanisms where applicable
- secure backups
- safe error reporting
- prevention of PII leakage to telemetry
- prevention of accidental PII disclosure to AI providers

No credentials, API keys, tokens, personal data, or sensitive information may be committed to the repository.

## 12. SaaS Readiness

Architect the application for multi-tenant SaaS operation.

Required where applicable:

- tenants / organizations
- users
- memberships
- roles and permissions
- tenant-scoped persistence
- strong tenant isolation
- subscription plans
- feature entitlements
- usage limits / quotas
- billing abstraction
- trial state
- subscription lifecycle
- upgrade/downgrade handling
- cancellation handling
- billing-event idempotency

Paid plans must be modeled explicitly rather than scattered through conditional statements.

Provider-specific billing logic must remain behind an abstraction boundary.

## 13. AI Coach

Make the system ready to expose an AI Coach capability.

The AI subsystem must be isolated behind explicit ports/interfaces.

Support:

- provider abstraction
- model configuration
- prompt/version tracking
- structured inputs/outputs where possible
- conversation/context management where applicable
- token/usage accounting
- cost tracking
- timeout/retry behavior
- safety boundaries
- PII controls
- auditability
- observability
- deterministic testing using provider fakes

Do not couple core domain logic directly to a specific LLM provider.

## 14. Auditability and Traceability

The system must provide end-to-end traceability for meaningful business operations.

Where relevant capture:

- actor
- tenant
- operation
- timestamp
- correlation/trace ID
- entity
- entity ID
- previous state
- resulting state
- originating request
- relevant domain event
- rule or decision responsible for the result

Audit records must be tamper-resistant from normal application flows and must not expose sensitive values unnecessarily.

## 15. Domain Evidence / Business-Rule Grounding

Every material business rule must be traceable to authoritative domain evidence.

For each important rule, capture when applicable:

- rule identifier
- description
- source/reference
- source version
- publication/effective date
- date last verified
- affected use cases
- implementation location
- supporting tests
- assumptions
- confidence level
- unresolved ambiguities

Prefer authoritative sources such as:

1. laws/regulations
2. official standards
3. official domain documentation
4. official provider specifications
5. primary-source publications
6. recognized domain references

Use independent domain oracles/checks where feasible to validate important calculations, decisions, limits, or regulatory rules.

Do not silently invent domain behavior when authoritative evidence is unavailable.

If sources disagree, explicitly document the disagreement and selected interpretation.

Business rules must be testable independently from frameworks and infrastructure.

## 16. Documentation and Developer Experience

Produce complete, accurate documentation covering:

- architecture
- bounded contexts
- domain model
- major design decisions
- local development
- installation
- Docker
- configuration
- environment variables
- database
- migrations
- fixtures/seeding
- testing
- REST API
- GraphQL API
- authentication
- authorization
- tenancy
- billing
- AI Coach
- MinIO/S3
- PII handling
- audit model
- observability
- production deployment
- troubleshooting
- backup/restore
- operational procedures

Create ADRs for significant architectural decisions.

The README must allow a new developer to clone the repository and successfully run the system without undocumented knowledge.

## 17. Client / SDK Enablement

Design the external API to support implementation of different clients without requiring internal application knowledge.

Provide:

- complete OpenAPI specification
- complete GraphQL schema
- authentication documentation
- error model
- pagination conventions
- examples
- versioning policy
- compatibility expectations
- webhook/event documentation if applicable

Where practical, provide or make possible generated SDKs for major client ecosystems.

## 18. Observability and Operations

Production readiness must include:

- structured logging
- correlation IDs
- health endpoint
- readiness endpoint
- metrics
- tracing hooks
- actionable error reporting
- dependency-health visibility
- startup diagnostics
- safe shutdown behavior
- backup/restore documentation
- migration operational procedures

Logs must never leak secrets or unnecessary PII.

## 19. Security

Apply production-grade security practices, including:

- dependency vulnerability checking
- secure defaults
- input validation
- authentication
- authorization
- tenant isolation
- rate-limiting strategy
- CORS policy
- CSRF protection where applicable
- SSRF protections where applicable
- secure file handling
- secure headers
- secrets outside source control
- protection against common OWASP API vulnerabilities

Security-critical behavior must have automated tests.

## 20. Quality Gates

Before declaring completion, prove that the repository passes all applicable quality gates.

At minimum:

- production build succeeds
- containers build successfully
- complete stack starts successfully
- migrations succeed from a clean database
- seed process succeeds
- unit tests pass
- integration tests pass
- E2E tests pass
- REST tests pass
- GraphQL tests pass
- coverage threshold passes
- linting passes
- formatting checks pass
- static/type analysis passes
- architectural dependency checks pass
- security checks pass
- no handwritten source file violates the 500 net LOC limit
- no known critical/high-severity vulnerability remains unexplained
- documentation reflects the actual implementation

Do not declare completion based solely on code inspection.

## 21. Definition of Done

The work is complete only when:

1. Every applicable requirement above has been implemented.
2. Every requirement has objective evidence showing that it passes.
3. All automated quality gates pass.
4. The full application can be bootstrapped from a clean environment.
5. The production deployment path is documented and validated as far as the available environment permits.
6. No known architectural violation remains undocumented.
7. No unresolved blocker prevents production use.

At completion, provide a concise final report containing:

- requirements satisfied
- architectural changes
- tests and coverage
- security status
- operational readiness
- documentation produced
- remaining non-blocking risks
- commands used to reproduce validation

## 22. Blocked Work

If any requirement cannot be completed because of unavailable information, credentials, infrastructure, external access, contradictory requirements, missing domain evidence, or another genuine blocker, do not fabricate a solution.

Create:

    block.txt

For every blocker include:

- Blocker ID
- Requirement affected
- Description
- Root cause
- Evidence
- What information/resource is missing
- Impact
- Whether other work can continue independently
- At least 3 concrete resolution options

For every resolution option include:

- proposed resolution
- implementation implications
- advantages
- disadvantages
- risks
- expected impact on architecture
- expected impact on schedule/complexity
- recommended/not recommended
- rationale

After recording a blocker, continue all work that is not actually blocked.

A blocker must never be used as a reason to stop unrelated improvements.

## 23. Final Principle

Do not optimize to make the repository merely appear compliant.

Optimize for a codebase that is demonstrably:

- correct
- secure
- domain-grounded
- maintainable
- testable
- observable
- auditable
- deployable
- operable
- extensible
- developer-friendly
- production-ready

Continue iterating until the Definition of Done is satisfied or every remaining unsatisfied requirement is explicitly documented in `block.txt` as genuinely blocked.