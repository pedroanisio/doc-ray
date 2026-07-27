import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Operation, OperationSchedule } from "../api/types";

const listOperations = vi.fn();
const listOperationSchedules = vi.fn();
const runOperation = vi.fn();
const createOperationSchedule = vi.fn();
const updateOperationSchedule = vi.fn();
const deleteOperationSchedule = vi.fn();

vi.mock("../services", () => ({ useApiClient: () => ({}) }));
vi.mock("../api/operations", () => ({
  listOperations: (...a: unknown[]) => listOperations(...a),
  listOperationSchedules: (...a: unknown[]) => listOperationSchedules(...a),
  runOperation: (...a: unknown[]) => runOperation(...a),
  createOperationSchedule: (...a: unknown[]) => createOperationSchedule(...a),
  updateOperationSchedule: (...a: unknown[]) => updateOperationSchedule(...a),
  deleteOperationSchedule: (...a: unknown[]) => deleteOperationSchedule(...a),
}));

import { OperationsPage } from "./OperationsPage";

function op(over: Partial<Operation> = {}): Operation {
  return {
    key: "visual_embed",
    label: "Visual embed (Tier 2)",
    jobKind: "visual_embed",
    lane: "gpu",
    summary: "CLIP towers over every extracted image.",
    completeness: "Skips documents that already have image embeddings.",
    expensive: false,
    defaultPriority: 500,
    pendingDocuments: 269,
    pendingUnits: 58387,
    unitLabel: "images",
    activeJobs: 0,
    lastStatus: "succeeded",
    lastFinishedAt: "2026-07-27T12:00:00Z",
    lastError: null,
    ...over,
  };
}

function schedule(over: Partial<OperationSchedule> = {}): OperationSchedule {
  return {
    id: "01SCHED",
    operationKey: "visual_embed",
    cadence: {
      kind: "daily",
      everyMinutes: null,
      hour: 2,
      minute: 0,
      weekday: null,
      dayOfMonth: null,
      description: "daily at 02:00 UTC",
    },
    onlyMissing: true,
    priority: 500,
    enabled: true,
    note: null,
    nextRunAt: "2026-07-28T02:00:00Z",
    lastRunAt: null,
    lastJobCount: null,
    lastError: null,
    createdAt: "2026-07-27T00:00:00Z",
    updatedAt: "2026-07-27T00:00:00Z",
    ...over,
  };
}

beforeEach(() => {
  listOperations.mockReset().mockResolvedValue([op()]);
  listOperationSchedules.mockReset().mockResolvedValue([schedule()]);
  runOperation.mockReset().mockResolvedValue({
    operationKey: "visual_embed",
    enqueued: 269,
    priority: 500,
    runAfter: null,
    jobIds: [],
  });
  createOperationSchedule.mockReset().mockResolvedValue(schedule());
  updateOperationSchedule.mockReset().mockResolvedValue(schedule({ enabled: false }));
  deleteOperationSchedule.mockReset().mockResolvedValue(true);
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

describe("OperationsPage", () => {
  it("shows the pending backlog in the operation's own unit", async () => {
    render(<OperationsPage />);
    expect(await screen.findByText(/269 docs · 58,387 images/)).toBeInTheDocument();
  });

  it("reports an operation with no backlog as up to date and blocks the run", async () => {
    listOperations.mockResolvedValue([op({ pendingDocuments: 0, pendingUnits: 0 })]);
    render(<OperationsPage />);
    expect(await screen.findByText("up to date")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run now" })).toBeDisabled();
  });

  it("runs an operation immediately when no start time is set", async () => {
    render(<OperationsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Run now" }));
    await waitFor(() => expect(runOperation).toHaveBeenCalled());
    expect(runOperation.mock.calls[0][1]).toMatchObject({
      key: "visual_embed",
      onlyMissing: true,
      runAfter: null,
    });
    expect(await screen.findByText(/enqueued 269 job\(s\) at priority 500/)).toBeInTheDocument();
  });

  it("defers the sweep when a start time is chosen", async () => {
    render(<OperationsPage />);
    const when = await screen.findByLabelText(/Start Visual embed/);
    await userEvent.type(when, "2026-07-28T02:00");
    await userEvent.click(screen.getByRole("button", { name: "Run later" }));
    await waitFor(() => expect(runOperation).toHaveBeenCalled());
    expect(runOperation.mock.calls[0][1].runAfter).toBe(
      new Date("2026-07-28T02:00").toISOString(),
    );
  });

  it("confirms before starting an expensive operation", async () => {
    listOperations.mockResolvedValue([
      op({ key: "visual_caption", label: "Visual caption (Tier 3)", expensive: true }),
    ]);
    vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<OperationsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Run now" }));
    expect(window.confirm).toHaveBeenCalled();
    expect(runOperation).not.toHaveBeenCalled();
  });

  it("creates a recurring schedule from the cadence form", async () => {
    render(<OperationsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Schedule…" }));
    await userEvent.click(screen.getByRole("button", { name: "Save schedule" }));
    await waitFor(() => expect(createOperationSchedule).toHaveBeenCalled());
    expect(createOperationSchedule.mock.calls[0][1]).toMatchObject({
      key: "visual_embed",
      cadence: { kind: "daily", hour: 2, minute: 0 },
    });
  });

  it("lists a schedule with its next firing time", async () => {
    render(<OperationsPage />);
    expect(await screen.findByText("daily at 02:00 UTC")).toBeInTheDocument();
  });

  it("toggles a schedule without deleting it", async () => {
    render(<OperationsPage />);
    await userEvent.click(await screen.findByRole("checkbox"));
    await waitFor(() => expect(updateOperationSchedule).toHaveBeenCalled());
    expect(updateOperationSchedule.mock.calls[0][2]).toEqual({ enabled: false });
  });

  it("deletes a schedule", async () => {
    render(<OperationsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
    await waitFor(() => expect(deleteOperationSchedule).toHaveBeenCalled());
    expect(deleteOperationSchedule.mock.calls[0][1]).toBe("01SCHED");
  });

  it("surfaces a server rejection instead of silently failing", async () => {
    runOperation.mockRejectedValue(new Error("interval cadence must be at least 5 minutes"));
    render(<OperationsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Run now" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/at least 5 minutes/);
  });

  it("shows the last failure reason for an operation", async () => {
    listOperations.mockResolvedValue([
      op({ lastStatus: "failed", lastError: "CUDA out of memory" }),
    ]);
    render(<OperationsPage />);
    expect(await screen.findByText(/CUDA out of memory/)).toBeInTheDocument();
  });
});
