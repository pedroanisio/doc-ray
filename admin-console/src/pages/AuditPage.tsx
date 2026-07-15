import { useState } from "react";
import { useApiClient } from "../services";
import { useAsync } from "../hooks/useAsync";
import { listAuditEvents, type AuditFilter } from "../api/audit";
import { PageHeader, AsyncBoundary } from "../components/common";

/** Security audit trail — admin-gated query.auditEvents. */
export function AuditPage() {
  const client = useApiClient();
  const [filter, setFilter] = useState<AuditFilter>({ limit: 100 });
  const [draft, setDraft] = useState<{ actor: string; action: string }>({ actor: "", action: "" });
  const events = useAsync(() => listAuditEvents(client, filter), [filter]);

  function apply() {
    setFilter({
      limit: 100,
      actor: draft.actor || undefined,
      action: draft.action || undefined,
    });
  }

  return (
    <div>
      <PageHeader
        title="Audit log"
        actions={
          <>
            <input
              placeholder="actor"
              value={draft.actor}
              onChange={(e) => setDraft({ ...draft, actor: e.target.value })}
            />
            <input
              placeholder="action"
              value={draft.action}
              onChange={(e) => setDraft({ ...draft, action: e.target.value })}
            />
            <button onClick={apply}>Filter</button>
          </>
        }
      />
      <AsyncBoundary state={events} empty="No audit events (admin scope required).">
        {(rows) => (
          <table className="grid">
            <thead>
              <tr>
                <th>When</th>
                <th>Actor</th>
                <th>Action</th>
                <th>Target</th>
                <th>Outcome</th>
                <th>Source IP</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((e) => (
                <tr key={e.id}>
                  <td className="mono">{new Date(e.occurredAt).toLocaleString()}</td>
                  <td>{e.actorSubject ?? e.actorPrincipalId ?? "—"}</td>
                  <td className="mono">{e.action}</td>
                  <td className="mono">
                    {e.targetType}
                    {e.targetId ? `:${e.targetId.slice(0, 8)}` : ""}
                  </td>
                  <td>
                    <span className={`badge outcome-${e.outcome}`}>{e.outcome}</span>
                  </td>
                  <td className="mono">{e.sourceIp ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </AsyncBoundary>
    </div>
  );
}
