import { useState } from "react";
import { useApiClient } from "../services";
import { useAsync } from "../hooks/useAsync";
import { AsyncBoundary, PageHeader } from "../components/common";
import { CadenceEditor, describeCadence } from "../components/CadenceEditor";
import {
  createOperationSchedule,
  deleteOperationSchedule,
  listOperationSchedules,
  listOperations,
  runOperation,
  updateOperationSchedule,
} from "../api/operations";
import type { CadenceInput, Operation, OperationSchedule } from "../api/types";

const DEFAULT_CADENCE: CadenceInput = { kind: "daily", hour: 2, minute: 0 };

function fmt(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "—";
}

/** Pending backlog phrased in the unit an operator actually sizes work by. */
function backlog(op: Operation): string {
  if (op.pendingDocuments === 0) return "up to date";
  const docs = `${op.pendingDocuments.toLocaleString()} doc${op.pendingDocuments === 1 ? "" : "s"}`;
  if (!op.unitLabel || op.pendingUnits === 0) return docs;
  return `${docs} · ${op.pendingUnits.toLocaleString()} ${op.unitLabel}`;
}

/**
 * The operations console: every corpus-wide maintenance action in one place,
 * with the three verbs an operator needs — see status, run, schedule.
 *
 * These operations were previously reachable only as CLI scripts on the host,
 * which made "is the corpus fully embedded?" a question nobody could answer from
 * a browser. Backlog counts here are computed from the same predicate the runner
 * uses, so the number shown is the number a run would enqueue.
 */
export function OperationsPage() {
  const client = useApiClient();
  const ops = useAsync(() => listOperations(client, true), [client]);
  const schedules = useAsync(() => listOperationSchedules(client), [client]);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [scheduling, setScheduling] = useState<string | null>(null);
  const [cadence, setCadence] = useState<CadenceInput>(DEFAULT_CADENCE);
  const [startAt, setStartAt] = useState<Record<string, string>>({});

  function reload() {
    ops.reload();
    schedules.reload();
  }

  async function act<T>(label: string, fn: () => Promise<T>, describe: (r: T) => string) {
    setBusy(label);
    setNotice(null);
    setError(null);
    try {
      const result = await fn();
      setNotice(describe(result));
      reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  async function onRun(op: Operation) {
    // An expensive sweep costs a VLM round trip per image across the whole
    // corpus; a misclick must not be able to start it.
    if (
      op.expensive &&
      !window.confirm(
        `${op.label} runs one model call per image across ${op.pendingDocuments} document(s). ` +
          "This is the most expensive work in the system. Continue?",
      )
    ) {
      return;
    }
    const when = startAt[op.key];
    await act(
      `run:${op.key}`,
      () =>
        runOperation(client, {
          key: op.key,
          onlyMissing: true,
          runAfter: when ? new Date(when).toISOString() : null,
        }),
      (run) =>
        `${op.label}: enqueued ${run.enqueued} job(s) at priority ${run.priority}` +
        (run.runAfter ? `, starting ${new Date(run.runAfter).toLocaleString()}` : "") +
        ".",
    );
  }

  async function onCreateSchedule(op: Operation) {
    await act(
      `schedule:${op.key}`,
      () => createOperationSchedule(client, { key: op.key, cadence, onlyMissing: true }),
      (row) => `Scheduled ${op.label} ${row.cadence.description}.`,
    );
    setScheduling(null);
    setCadence(DEFAULT_CADENCE);
  }

  return (
    <div>
      <PageHeader
        title="Operations"
        actions={
          <button type="button" onClick={reload} disabled={busy !== null}>
            Refresh
          </button>
        }
      />

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

      <p className="muted">
        Corpus-wide maintenance. Runs enqueue ordinary jobs at a background priority,
        so user-facing pipeline work always preempts them — a sweep fills idle
        capacity rather than blocking ingest. Track progress on the Jobs page.
      </p>

      <AsyncBoundary state={ops} empty="No operations registered.">
        {(rows) => (
          <table className="data-table">
            <thead>
              <tr>
                <th>Operation</th>
                <th>Lane</th>
                <th>Pending</th>
                <th>Active</th>
                <th>Last run</th>
                <th>Run</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((op) => (
                <tr key={op.key}>
                  <td>
                    <strong>{op.label}</strong>
                    {op.expensive && <span className="badge warn"> expensive</span>}
                    <div className="muted small">{op.summary}</div>
                  </td>
                  <td>
                    <span className={`badge ${op.lane}`}>{op.lane.toUpperCase()}</span>
                  </td>
                  <td>{backlog(op)}</td>
                  <td>{op.activeJobs || "—"}</td>
                  <td>
                    {op.lastStatus ? (
                      <>
                        <span className={`badge ${op.lastStatus}`}>{op.lastStatus}</span>
                        <div className="muted small">{fmt(op.lastFinishedAt)}</div>
                        {op.lastError && (
                          <div className="muted small" title={op.lastError}>
                            {op.lastError.slice(0, 80)}
                          </div>
                        )}
                      </>
                    ) : (
                      <span className="muted">never</span>
                    )}
                  </td>
                  <td className="actions">
                    <label className="sr-only" htmlFor={`start-${op.key}`}>
                      Start {op.label} at
                    </label>
                    <input
                      id={`start-${op.key}`}
                      type="datetime-local"
                      value={startAt[op.key] ?? ""}
                      onChange={(e) =>
                        setStartAt((prev) => ({ ...prev, [op.key]: e.target.value }))
                      }
                      title="Leave empty to start immediately"
                    />
                    <button
                      type="button"
                      onClick={() => onRun(op)}
                      disabled={busy !== null || op.pendingDocuments === 0}
                    >
                      {startAt[op.key] ? "Run later" : "Run now"}
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        setScheduling((current) => (current === op.key ? null : op.key))
                      }
                      disabled={busy !== null}
                    >
                      Schedule…
                    </button>
                    {scheduling === op.key && (
                      <div className="schedule-form">
                        <CadenceEditor
                          value={cadence}
                          onChange={setCadence}
                          idPrefix={`new-${op.key}`}
                        />
                        <button
                          type="button"
                          onClick={() => onCreateSchedule(op)}
                          disabled={busy !== null}
                        >
                          Save schedule
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </AsyncBoundary>

      <h2>Schedules</h2>
      <AsyncBoundary state={schedules} empty="No recurring schedules.">
        {(rows) => (
          <table className="data-table">
            <thead>
              <tr>
                <th>Operation</th>
                <th>Cadence</th>
                <th>Next run</th>
                <th>Last run</th>
                <th>Enabled</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row: OperationSchedule) => (
                <tr key={row.id}>
                  <td>{row.operationKey}</td>
                  <td>{row.cadence.description || describeCadence(row.cadence)}</td>
                  <td>{fmt(row.nextRunAt)}</td>
                  <td>
                    {fmt(row.lastRunAt)}
                    {row.lastJobCount !== null && (
                      <div className="muted small">{row.lastJobCount} job(s)</div>
                    )}
                    {row.lastError && (
                      <div role="alert" className="muted small">
                        {row.lastError.slice(0, 80)}
                      </div>
                    )}
                  </td>
                  <td>
                    <input
                      type="checkbox"
                      checked={row.enabled}
                      aria-label={`Enable ${row.operationKey} schedule`}
                      disabled={busy !== null}
                      onChange={(e) =>
                        act(
                          `toggle:${row.id}`,
                          () =>
                            updateOperationSchedule(client, row.id, {
                              enabled: e.target.checked,
                            }),
                          (updated) =>
                            `${updated.operationKey} schedule ${
                              updated.enabled ? "enabled" : "disabled"
                            }.`,
                        )
                      }
                    />
                  </td>
                  <td className="actions">
                    <button
                      type="button"
                      disabled={busy !== null}
                      onClick={() =>
                        act(
                          `delete:${row.id}`,
                          () => deleteOperationSchedule(client, row.id),
                          () => `Deleted the ${row.operationKey} schedule.`,
                        )
                      }
                    >
                      Delete
                    </button>
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
