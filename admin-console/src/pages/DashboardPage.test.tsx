import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import type { OperatorHealth } from "../api/types";

const fetchLiveness = vi.fn();
const fetchReadiness = vi.fn();
const fetchOperatorHealth = vi.fn();

vi.mock("../services", () => ({ useApiClient: () => ({}) }));
vi.mock("../api/ops", () => ({
  fetchLiveness: (...a: unknown[]) => fetchLiveness(...a),
  fetchReadiness: (...a: unknown[]) => fetchReadiness(...a),
  fetchOperatorHealth: (...a: unknown[]) => fetchOperatorHealth(...a),
}));

import { DashboardPage } from "./DashboardPage";

// A faithful operator-health.v1 payload (webapp/health_contract.py). Metrics are
// nested under corpus.totals; jobs are a (kind,status,count) spool; issues live in
// BOTH corpus and database and carry `detail`, not `message`.
function v1(): OperatorHealth {
  return {
    schema_version: "operator-health.v1",
    corpus: {
      totals: {
        documents: 1234,
        sentences: 56789,
        tokens: 42,
        entity_mentions: 5,
        entity_links: 4,
        canonical_entries: 3,
        lexemes: 2,
        embeddings: 1,
      },
      issues: [{ code: "no_sentences", severity: "warning", detail: "12 documents have no sentences" }],
      issue_groups: [],
      jobs: [
        { kind: "annotate", status: "running", count: 30 },
        { kind: "extract", status: "failed", count: 11 },
      ],
      job_spool: [],
    },
    database: {
      size: { bytes: 100, pretty: "100 bytes" },
      connections: { used: 2, max: 100, states: [] },
      tables: [],
      issues: [{ code: "table_bloat", severity: "info", detail: "table chunk is 30% dead rows" }],
      issue_groups: [],
    },
  };
}

beforeEach(() => {
  fetchLiveness.mockReset().mockResolvedValue({ status: "ok", version: "9.9.9" });
  fetchReadiness.mockReset().mockResolvedValue({ ready: true });
  fetchOperatorHealth.mockReset().mockResolvedValue(v1());
});

describe("DashboardPage operator-health panel", () => {
  it("renders corpus totals from corpus.totals (defect 3: not the flat top level)", async () => {
    render(<DashboardPage />);
    await waitFor(() => expect(screen.getByText("documents")).toBeInTheDocument());
    expect(screen.getByText("1234")).toBeInTheDocument();
    expect(screen.getByText("sentences")).toBeInTheDocument();
    expect(screen.getByText("56789")).toBeInTheDocument();
  });

  it("renders the job spool from corpus.jobs (kind/status/count)", async () => {
    render(<DashboardPage />);
    await waitFor(() => expect(screen.getByText("annotate/running")).toBeInTheDocument());
    expect(screen.getByText("30")).toBeInTheDocument();
    expect(screen.getByText("extract/failed")).toBeInTheDocument();
    expect(screen.getByText("11")).toBeInTheDocument();
  });

  it("renders issues from BOTH corpus and database using `detail` (not `message`)", async () => {
    render(<DashboardPage />);
    await waitFor(() =>
      expect(screen.getByText("12 documents have no sentences")).toBeInTheDocument(),
    );
    expect(screen.getByText("table chunk is 30% dead rows")).toBeInTheDocument();
    // The v1 issue code is surfaced too.
    expect(screen.getByText(/no_sentences/)).toBeInTheDocument();
  });

  it("shows the actionable error verbatim and NOT the hard-coded token suffix (defect 4)", async () => {
    fetchOperatorHealth.mockRejectedValue(
      new Error(
        "expected JSON from /operator/health.json but received text/html (HTTP 200). " +
          "Check that /operator/health.json is reverse-proxied to the doc-ray API/webapp.",
      ),
    );
    render(<DashboardPage />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("alert").textContent).toMatch(/reverse-proxied/);
    // The misleading blanket suffix must be gone (it mislabeled routing as a token bug).
    expect(
      screen.queryByText(/requires DOCRAY_OPERATOR_HEALTH_TOKEN configured on the webapp proxy/),
    ).toBeNull();
  });
});
