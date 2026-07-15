import { useState } from "react";
import { useApiClient } from "../services";
import { useAsync } from "../hooks/useAsync";
import { listDocuments } from "../api/documents";
import { PageHeader, AsyncBoundary } from "../components/common";

/** Read-only corpus overview (RLS in Postgres scopes visibility). */
export function DocumentsPage() {
  const client = useApiClient();
  const [status, setStatus] = useState("");
  const docs = useAsync(
    () => listDocuments(client, { limit: 50, status: status || undefined }),
    [status],
  );

  return (
    <div>
      <PageHeader
        title="Documents"
        actions={
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            <option value="ready">ready</option>
            <option value="processing">processing</option>
            <option value="failed">failed</option>
          </select>
        }
      />
      <AsyncBoundary state={docs} empty="No documents match.">
        {(rows) => (
          <table className="grid">
            <thead>
              <tr>
                <th>Title</th>
                <th>Type</th>
                <th>Status</th>
                <th>Lifecycle</th>
                <th>Lang</th>
                <th>Chars</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((d) => (
                <tr key={d.id}>
                  <td>{d.title}</td>
                  <td>{d.docType}</td>
                  <td>
                    <span className={`badge status-${d.status}`}>{d.status}</span>
                  </td>
                  <td>{d.lifecycleState}</td>
                  <td>{d.language ?? "—"}</td>
                  <td className="mono">{d.charCount?.toLocaleString() ?? "—"}</td>
                  <td className="mono">{new Date(d.createdAt).toLocaleDateString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </AsyncBoundary>
    </div>
  );
}
