import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Job } from "../api/types";

const listJobs = vi.fn();
const cancelJob = vi.fn();
const reextractAllDocuments = vi.fn();

vi.mock("../services", () => ({ useApiClient: () => ({}) }));
vi.mock("../api/jobs", () => ({
  listJobs: (...a: unknown[]) => listJobs(...a),
  cancelJob: (...a: unknown[]) => cancelJob(...a),
  reextractAllDocuments: (...a: unknown[]) => reextractAllDocuments(...a),
}));

import { JobsPage } from "./JobsPage";

function job(id: string, over: Partial<Job> = {}): Job {
  return {
    id,
    kind: "annotate",
    status: "succeeded",
    documentId: null,
    createdAt: "2026-07-25T00:00:00Z",
    startedAt: null,
    finishedAt: null,
    attempts: 1,
    maxAttempts: 3,
    error: null,
    progressCurrent: null,
    progressTotal: null,
    progressStage: null,
    cursor: id,
    ...over,
  };
}

function lastVars() {
  return listJobs.mock.calls[listJobs.mock.calls.length - 1][1];
}

beforeEach(() => {
  listJobs.mockReset();
  listJobs.mockResolvedValue([job("a"), job("b")]);
});

describe("JobsPage", () => {
  it("loads jobs with no filter on mount and renders them", async () => {
    render(<JobsPage />);
    await waitFor(() => expect(screen.getByText("Kind")).toBeInTheDocument());
    const vars = lastVars();
    expect(vars.status).toBeUndefined();
    expect(vars.kind).toBeUndefined();
    expect(vars.limit).toBe(25);
    // two data rows rendered (header row + 2 body rows); "annotate" also appears
    // in the kind filter, so assert on table rows, not raw text.
    expect(screen.getAllByRole("row").slice(1)).toHaveLength(2);
  });

  it("refetches with the selected status filter", async () => {
    render(<JobsPage />);
    await waitFor(() => expect(listJobs).toHaveBeenCalled());
    await userEvent.selectOptions(screen.getByLabelText("Filter by status"), "failed");
    await waitFor(() => expect(lastVars().status).toBe("failed"));
  });

  it("refetches with the selected kind filter", async () => {
    render(<JobsPage />);
    await waitFor(() => expect(listJobs).toHaveBeenCalled());
    await userEvent.selectOptions(screen.getByLabelText("Filter by kind"), "backup");
    await waitFor(() => expect(lastVars().kind).toBe("backup"));
  });

  it("Next pages forward using the last row's cursor when a full page is returned", async () => {
    const fullPage = Array.from({ length: 25 }, (_, i) => job(`j${i}`, { cursor: `cur-${i}` }));
    listJobs.mockResolvedValue(fullPage);
    render(<JobsPage />);
    await waitFor(() => expect(screen.getByText("Next →")).toBeEnabled());
    await userEvent.click(screen.getByText("Next →"));
    await waitFor(() => expect(lastVars().after).toBe("cur-24"));
  });

  it("Next is disabled and Previous starts disabled on a partial first page", async () => {
    render(<JobsPage />); // default mock returns 2 rows (< PAGE_SIZE)
    await waitFor(() => expect(screen.getByText("Next →")).toBeInTheDocument());
    expect(screen.getByText("Next →")).toBeDisabled();
    expect(screen.getByText("← Previous")).toBeDisabled();
  });

  it("shows the filter-aware empty state when no jobs match", async () => {
    listJobs.mockResolvedValue([]);
    render(<JobsPage />);
    await waitFor(() =>
      expect(screen.getByText("No jobs match this filter.")).toBeInTheDocument(),
    );
  });
});
