import { useState } from "react";
import { useApiClient } from "../services";
import { useAsync } from "../hooks/useAsync";
import { listJobs, cancelJob, reextractAllDocuments } from "../api/jobs";
import { PageHeader, AsyncBoundary } from "../components/common";

/** Job queue: monitor status and run the admin-gated operations. */
export function JobsPage() {
  const client = useApiClient();
  const jobs = useAsync(() => listJobs(client, { limit: 50 }), []);
  const [notice, setNotice] = useState<string | null>(null);

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
      jobs.reload();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : String(err));
    }
  }

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
      {notice && (
        <p role="status" className="alert info">
          {notice}
        </p>
      )}
      <AsyncBoundary state={jobs} empty="No jobs in the queue.">
        {(rows) => (
          <table className="grid">
            <thead>
              <tr>
                <th>Kind</th>
                <th>Status</th>
                <th>Progress</th>
                <th>Attempts</th>
                <th>Created</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((job) => (
                <tr key={job.id}>
                  <td>{job.kind}</td>
                  <td>
                    <span className={`badge status-${job.status}`}>{job.status}</span>
                  </td>
                  <td>
                    {job.progressTotal
                      ? `${job.progressCurrent ?? 0}/${job.progressTotal}${
                          job.progressStage ? ` · ${job.progressStage}` : ""
                        }`
                      : "—"}
                  </td>
                  <td>
                    {job.attempts}/{job.maxAttempts}
                  </td>
                  <td className="mono">{new Date(job.createdAt).toLocaleString()}</td>
                  <td>
                    {["queued", "running", "pending"].includes(job.status) && (
                      <button className="danger" onClick={() => onCancel(job.id)}>
                        Cancel
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </AsyncBoundary>
    </div>
  );
}
