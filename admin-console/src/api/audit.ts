/**
 * Security audit trail — query.py::audit_events (admin-gated by
 * security_audit_read.require_audit_admin). The console surfaces the same
 * filters the backend accepts so an operator can trace an actor or action.
 */
import { ApiClient } from "./client";
import type { SecurityAuditEvent } from "./types";

const EVENT_FIELDS = `
  id occurredAt tenantId actorPrincipalId actorSubject action
  targetType targetId outcome sourceIp requestId
`;

export interface AuditFilter {
  actor?: string;
  action?: string;
  targetType?: string;
  targetId?: string;
  occurredAfter?: string;
  occurredBefore?: string;
  sourceIp?: string;
  limit?: number;
  after?: string;
}

export async function listAuditEvents(
  client: ApiClient,
  filter: AuditFilter = {},
): Promise<SecurityAuditEvent[]> {
  const data = await client.graphql<{ auditEvents: SecurityAuditEvent[] }>(
    `query Audit(
       $actor: String, $action: String, $targetType: String, $targetId: String,
       $occurredAfter: String, $occurredBefore: String, $sourceIp: String,
       $limit: Int, $after: String
     ) {
       auditEvents(
         actor: $actor, action: $action, targetType: $targetType, targetId: $targetId,
         occurredAfter: $occurredAfter, occurredBefore: $occurredBefore, sourceIp: $sourceIp,
         limit: $limit, after: $after
       ) { ${EVENT_FIELDS} }
     }`,
    {
      actor: filter.actor ?? null,
      action: filter.action ?? null,
      targetType: filter.targetType ?? null,
      targetId: filter.targetId ?? null,
      occurredAfter: filter.occurredAfter ?? null,
      occurredBefore: filter.occurredBefore ?? null,
      sourceIp: filter.sourceIp ?? null,
      limit: filter.limit ?? 100,
      after: filter.after ?? null,
    },
  );
  return data.auditEvents;
}
