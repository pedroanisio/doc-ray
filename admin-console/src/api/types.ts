/**
 * Types mirrored VERBATIM from the doc-ray API contract. Field names match the
 * GraphQL schema (camelCased Strawberry types) and the REST JSON bodies exactly
 * — no invented fields. Sources are cited per block so drift can be audited.
 *
 * ⚠ ARCHITECTURAL CONTRACT (PALS's LAW): these types describe an untrusted wire
 * format. The client layer validates shape at the boundary; downstream code
 * must not assume the server honored the contract.
 */

// POST /auth/{login,token,refresh} response — auth_routes.py::_issue_jwt_pair
export interface JwtPairResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  refresh_expires_in: number;
}

// GraphQL type Job — src/doc_ray/entrypoints/api/types.py
export interface Job {
  id: string;
  kind: string;
  status: string;
  documentId: string | null;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  attempts: number;
  maxAttempts: number;
  error: string | null;
  progressCurrent: number | null;
  progressTotal: number | null;
  progressStage: string | null;
  cursor: string;
}

// GraphQL type SecurityAuditEvent — security_audit_types.py
export interface SecurityAuditEvent {
  id: string;
  occurredAt: string;
  tenantId: string | null;
  actorPrincipalId: string | null;
  actorSubject: string | null;
  action: string;
  targetType: string;
  targetId: string | null;
  outcome: string;
  sourceIp: string | null;
  requestId: string | null;
}

// GraphQL type Document (subset the console renders) — document_types.py
export interface Document {
  id: string;
  title: string;
  docType: string;
  status: string;
  lifecycleState: string;
  language: string | null;
  charCount: number | null;
  createdAt: string;
  updatedAt: string | null;
  /** Why a `failed` document failed; null for every other status. */
  error: string | null;
}

// webapp GET /operator/health.json — the `operator-health.v1` contract in
// webapp/health_contract.py (operator_health_payload). Mirrored EXACTLY: metrics
// are nested under corpus.totals, jobs are a (kind,status,count) spool summary,
// and issues live in BOTH corpus and database carrying `detail` (not `message`).
// A drift guard cross-checks this against health_contract.py — see
// src/doc_ray/tests/test_admin_console_contract.py.
export interface OperatorHealthTotals {
  documents: number;
  sentences: number;
  tokens: number;
  entity_mentions: number;
  entity_links: number;
  canonical_entries: number;
  lexemes: number;
  embeddings: number;
  // The snapshot may gain totals over time; every value is a count.
  [key: string]: number;
}

export interface OperatorJobCount {
  kind: string;
  status: string;
  count: number;
}

export interface OperatorHealthCorpus {
  totals: OperatorHealthTotals;
  issues: OperatorIssue[];
  issue_groups: unknown[];
  jobs: OperatorJobCount[];
  job_spool: unknown[];
}

export interface OperatorHealthDatabase {
  size: { bytes: number; pretty: string };
  connections: { used: number; max: number; states: { state: string; count: number }[] };
  tables: unknown[];
  issues: OperatorIssue[];
  issue_groups: unknown[];
}

export interface OperatorHealth {
  schema_version: string;
  corpus: OperatorHealthCorpus;
  database: OperatorHealthDatabase;
  [key: string]: unknown;
}

export interface OperatorIssue {
  code: string;
  severity: string;
  detail: string;
  document_id?: string;
  [key: string]: unknown;
}

// GET /health and /readyz — api/health.py
export interface LivenessProbe {
  status: string;
  version?: string;
}
