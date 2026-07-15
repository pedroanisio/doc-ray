import { useState } from "react";
import { useApiClient } from "../services";
import { createBackup, restoreBackup } from "../api/backups";
import { PageHeader } from "../components/common";

/**
 * Backup operations. create/restore are admin-gated GraphQL mutations that
 * enqueue a job. There is no list-backups API (the webapp reads the table via
 * SQL), so this page performs actions and links to the job it enqueues rather
 * than fabricating a backup inventory.
 */
export function BackupsPage() {
  const client = useApiClient();
  const [note, setNote] = useState("");
  const [restoreId, setRestoreId] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onCreate() {
    setNotice(null);
    setError(null);
    try {
      const job = await createBackup(client, note || undefined);
      setNotice(`Backup job enqueued: ${job.id}`);
      setNote("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function onRestore() {
    setNotice(null);
    setError(null);
    if (!restoreId.trim()) {
      setError("Enter a backup id to restore.");
      return;
    }
    try {
      const job = await restoreBackup(client, restoreId.trim());
      setNotice(`Restore job enqueued: ${job.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div>
      <PageHeader title="Backups" />
      {notice && (
        <p role="status" className="alert info">
          {notice}
        </p>
      )}
      {error && (
        <p role="alert" className="alert">
          {error}
        </p>
      )}

      <section className="card">
        <h2>Create backup</h2>
        <p className="muted">Enqueues a full-corpus archive job (admin only).</p>
        <div className="row">
          <input
            placeholder="note (optional)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <button onClick={onCreate}>Create backup</button>
        </div>
      </section>

      <section className="card">
        <h2>Restore backup</h2>
        <p className="muted">
          Danger: restoring overwrites the live corpus. Backup ids are listed in the webapp
          <span className="mono"> /backups</span> page (no list API exists yet).
        </p>
        <div className="row">
          <input
            placeholder="backup id"
            value={restoreId}
            onChange={(e) => setRestoreId(e.target.value)}
          />
          <button className="danger" onClick={onRestore}>
            Restore
          </button>
        </div>
      </section>
    </div>
  );
}
