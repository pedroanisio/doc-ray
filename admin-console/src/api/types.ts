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
}

// webapp GET /operator/health.json — webapp/operator_health.py contract.
// Kept intentionally open (Record) beyond the fields the dashboard reads,
// because the operator-health payload is an evolving operational snapshot.
export interface OperatorHealth {
  corpus?: Record<string, number>;
  jobs?: Record<string, number>;
  database?: Record<string, unknown>;
  issues?: OperatorIssue[];
  issue_groups?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface OperatorIssue {
  code?: string;
  severity?: string;
  message?: string;
  [key: string]: unknown;
}

// GET /health and /readyz — api/health.py
export interface LivenessProbe {
  status: string;
  version?: string;
}
