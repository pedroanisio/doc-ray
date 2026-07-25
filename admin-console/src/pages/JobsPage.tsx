import { useState } from "react";
import { useApiClient } from "../services";
import { useAsync } from "../hooks/useAsync";
import { listJobs, cancelJob, reextractAllDocuments } from "../api/jobs";
import { PageHeader, AsyncBoundary } from "../components/common";
import { DataTable, type Column } from "../components/DataTable";
import type { Job } from "../api/types";

const PAGE_SIZE = 25;
const STATUSES = ["queued", "running", "succeeded", "failed", "cancelled"] as const;
const KINDS = [
  "ingest",
  "annotate",
  "reannotate",
  "semantic_enrich",
  "semantic_embed",
  "canonicalize",
  "visual_extract",
  "update_metadata",
  "backup",
  "restore",
  "apply_corrections",
] as const;

/** Job queue: filter + paginate the history, and run the admin-gated operations. */
export function JobsPage() {
  const client = useApiClient();
  const [status, setStatus] = useState("");
  const [kind, setKind] = useState("");
  const [after, setAfter] = useState<string | null>(null);
  const [history, setHistory] = useState<(string | null)[]>([]);
  const [notice, setNotice] = useState<string | null>(null);

  const jobs = useAsync<Job[]>(
    () =>
      listJobs(client, {
        status: status || undefined,
        kind: kind || undefined,
        limit: PAGE_SIZE,
        after: after ?? undefined,
      }),
    [status, kind, after],
  );

  function resetPaging() {
    setAfter(null);
    setHistory([]);
  }

  function onStatus(next: string) {
    setStatus(next);
    resetPaging();
  }
  function onKind(next: string) {
    setKind(next);
    resetPaging();
  }

  function onNext(rows: Job[]) {
    if (rows.length < PAGE_SIZE) return;
    setHistory((h) => [...h, after]);
    setAfter(rows[rows.length - 1].cursor);
  }
  function onPrev() {
    setHistory((h) => {
      const copy = [...h];
      const prev = copy.pop() ?? null;
      setAfter(prev);
      return copy;
    });
  }

  async function onCancel(id: string) {
    setNotice(null);
    try {
      await cancelJob(client, id, "cancelled from admin console");
      jobs.reload();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : String(err));
    }
  }

  async function onReextract() {
    setNotice(null);
    try {
      const enqueued = await reextractAllDocuments(client, true);
      setNotice(`Enqueued ${enqueued.length} re-extract job(s).`);
      resetPaging();
      jobs.reload();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : String(err));
    }
  }

  const columns: Column<Job>[] = [
    { key: "kind", header: "Kind" },
    {
      key: "status",
      header: "Status",
      render: (j) => <span className={`badge status-${j.status}`}>{j.status}</span>,
    },
    {
      key: "progressStage",
      header: "Progress",
      render: (j) =>
        j.progressTotal
          ? `${j.progressCurrent ?? 0}/${j.progressTotal}${
              j.progressStage ? ` · ${j.progressStage}` : ""
            }`
          : "—",
    },
    { key: "attempts", header: "Attempts", render: (j) => `${j.attempts}/${j.maxAttempts}` },
    { key: "error", header: "Error", render: (j) => j.error ?? "—", className: "mono" },
    {
      key: "createdAt",
      header: "Created",
      render: (j) => new Date(j.createdAt).toLocaleString(),
      className: "mono",
    },
    {
      key: "id",
      header: "",
      render: (j) =>
        ["queued", "running", "pending"].includes(j.status) ? (
          <button className="danger" onClick={() => onCancel(j.id)}>
            Cancel
          </button>
        ) : null,
    },
  ];

  return (
    <div>
      <PageHeader
        title="Jobs"
        actions={
          <>
            <button onClick={onReextract} title="Admin-gated: reextractDocuments">
              Re-extract (missing geometry)
            </button>
            <button onClick={jobs.reload}>Refresh</button>
          </>
        }
      />

      <div className="filters" role="search">
        <label>
          Status
          <select
            aria-label="Filter by status"
            value={status}
            onChange={(e) => onStatus(e.target.value)}
          >
            <option value="">All statuses</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label>
          Kind
          <select aria-label="Filter by kind" value={kind} onChange={(e) => onKind(e.target.value)}>
            <option value="">All kinds</option>
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </label>
      </div>

      {notice && (
        <p role="status" className="alert info">
          {notice}
        </p>
      )}

      <AsyncBoundary state={jobs} empty="No jobs match this filter.">
        {(rows) => (
          <>
            <DataTable columns={columns} rows={rows} rowKey={(j) => j.id} />
            <nav className="pager" aria-label="Pagination">
              <button onClick={onPrev} disabled={history.length === 0}>
                ← Previous
              </button>
              <button onClick={() => onNext(rows)} disabled={rows.length < PAGE_SIZE}>
                Next →
              </button>
            </nav>
          </>
        )}
      </AsyncBoundary>
    </div>
  );
}
