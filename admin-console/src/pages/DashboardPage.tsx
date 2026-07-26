import { useApiClient } from "../services";
import { useAsync } from "../hooks/useAsync";
import { fetchLiveness, fetchReadiness, fetchOperatorHealth } from "../api/ops";
import { PageHeader } from "../components/common";
import type { OperatorIssue, OperatorJobCount } from "../api/types";

/**
 * Operational overview: liveness, readiness, and the webapp operator-health
 * snapshot (corpus totals, job spool, DB stats, and any surfaced issues).
 */
export function DashboardPage() {
  const client = useApiClient();
  const liveness = useAsync(() => fetchLiveness(client), []);
  const readiness = useAsync(() => fetchReadiness(client), []);
  const health = useAsync(() => fetchOperatorHealth(client), []);

  return (
    <div>
      <PageHeader
        title="Operational status"
        actions={
          <button
            onClick={() => {
              liveness.reload();
              readiness.reload();
              health.reload();
            }}
          >
            Refresh
          </button>
        }
      />

      <div className="tiles">
        <StatusTile
          label="API liveness"
          ok={liveness.data?.status === "ok"}
          detail={liveness.error ?? liveness.data?.version ?? "—"}
        />
        <StatusTile
          label="Readiness (DB + object store)"
          ok={readiness.data?.ready === true}
          detail={readiness.error ?? (readiness.data?.ready ? "ready" : "not ready")}
        />
      </div>

      <section className="card">
        <h2>Operator health snapshot</h2>
        {health.loading && health.data === null && <p className="muted">Loading…</p>}
        {health.error && (
          // Show the error verbatim: client.rest() / fetchOperatorHealth already
          // produce a cause-accurate message (routing/parse vs 404-token vs 5xx).
          // The old blanket "requires DOCRAY_OPERATOR_HEALTH_TOKEN" suffix
          // mislabeled every failure as a token bug (#22, defect 4).
          <p role="alert" className="alert">
            {health.error}
          </p>
        )}
        {health.data && (
          <>
            <MetricGrid title="Corpus" metrics={health.data.corpus?.totals} />
            <MetricGrid title="Jobs" metrics={jobsToMetrics(health.data.corpus?.jobs)} />
            <IssueList issues={collectIssues(health.data)} />
          </>
        )}
      </section>
    </div>
  );
}

function StatusTile({ label, ok, detail }: { label: string; ok: boolean; detail: string }) {
  return (
    <div className={`tile ${ok ? "tile-ok" : "tile-bad"}`}>
      <div className="tile-label">{label}</div>
      <div className="tile-value">{ok ? "Healthy" : "Attention"}</div>
      <div className="tile-detail">{detail}</div>
    </div>
  );
}

function MetricGrid({ title, metrics }: { title: string; metrics?: Record<string, number> }) {
  if (!metrics || Object.keys(metrics).length === 0) return null;
  return (
    <div className="metric-block">
      <h3>{title}</h3>
      <div className="metric-grid">
        {Object.entries(metrics).map(([k, v]) => (
          <div key={k} className="metric">
            <span className="metric-num">{v}</span>
            <span className="metric-key">{k}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * The v1 job spool is a list of (kind, status, count) rows. Fold it into a
 * `kind/status → count` metric map so the existing MetricGrid renders it.
 * Defensive against a missing/renamed field — the wire format is untrusted.
 */
function jobsToMetrics(jobs?: OperatorJobCount[]): Record<string, number> {
  if (!jobs) return {};
  const out: Record<string, number> = {};
  for (const j of jobs) {
    out[`${j.kind ?? "unknown"}/${j.status ?? "unknown"}`] = j.count ?? 0;
  }
  return out;
}

/** v1 issues live in BOTH corpus and database; the panel surfaces them together. */
function collectIssues(data: { corpus?: { issues?: OperatorIssue[] }; database?: { issues?: OperatorIssue[] } }): OperatorIssue[] {
  return [...(data.corpus?.issues ?? []), ...(data.database?.issues ?? [])];
}

function IssueList({ issues }: { issues: OperatorIssue[] }) {
  if (issues.length === 0) return null;
  return (
    <div className="issues">
      <h3>Issues</h3>
      <ul>
        {issues.map((issue, i) => (
          <li key={i} className={`issue sev-${issue.severity ?? "info"}`}>
            <strong>{issue.code ?? issue.severity ?? "issue"}</strong>{" "}
            {issue.detail ?? JSON.stringify(issue)}
          </li>
        ))}
      </ul>
    </div>
  );
}
