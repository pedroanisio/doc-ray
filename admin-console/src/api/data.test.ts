import { describe, it, expect } from "vitest";
import { ApiClient } from "./client";
import { jsonResponse, makeFetchMock } from "../test/http";
import { fetchLiveness, fetchReadiness, fetchOperatorHealth } from "./ops";
import { listJobs, cancelJob, reextractAllDocuments } from "./jobs";
import { listDocuments } from "./documents";
import { listAuditEvents } from "./audit";
import { createBackup, restoreBackup } from "./backups";

function clientReturning(body: unknown, status = 200) {
  const fetchImpl = makeFetchMock(async () => jsonResponse(body, { status }));
  const client = new ApiClient({
    baseUrl: "",
    fetchImpl,
    getToken: () => "tok",
    refresh: async () => false,
  });
  return { client, fetchImpl };
}

describe("ops module", () => {
  it("fetchLiveness reads GET /health", async () => {
    const { client, fetchImpl } = clientReturning({ status: "ok", version: "1.0" });
    expect(await fetchLiveness(client)).toEqual({ status: "ok", version: "1.0" });
    expect(fetchImpl.mock.calls[0][0]).toBe("/health");
  });

  it("fetchReadiness reports ready=false on a 503 without throwing", async () => {
    const { client } = clientReturning({ status: "not ready" }, 503);
    const result = await fetchReadiness(client);
    expect(result.ready).toBe(false);
  });

  it("fetchReadiness reports ready=true on 200", async () => {
    const { client } = clientReturning({ status: "ok" }, 200);
    expect((await fetchReadiness(client)).ready).toBe(true);
  });

  it("fetchOperatorHealth reads the webapp operator snapshot", async () => {
    const { client, fetchImpl } = clientReturning({ corpus: { documents: 12 }, issues: [] });
    const health = await fetchOperatorHealth(client);
    expect(health.corpus?.documents).toBe(12);
    expect(fetchImpl.mock.calls[0][0]).toBe("/operator/health.json");
  });
});

describe("jobs module", () => {
  it("listJobs unwraps the GraphQL jobs array", async () => {
    const { client } = clientReturning({ data: { jobs: [{ id: "j1", kind: "annotate", status: "running" }] } });
    const jobs = await listJobs(client, { limit: 10 });
    expect(jobs).toHaveLength(1);
    expect(jobs[0].id).toBe("j1");
  });

  it("cancelJob sends the admin mutation with the job id and reason", async () => {
    const { client, fetchImpl } = clientReturning({ data: { cancelJob: { id: "j1", status: "cancelled" } } });
    const job = await cancelJob(client, "j1", "stuck");
    expect(job.status).toBe("cancelled");
    const body = JSON.parse(fetchImpl.mock.calls[0][1].body!);
    expect(body.variables).toEqual({ jobId: "j1", reason: "stuck" });
    expect(body.query).toContain("cancelJob");
  });

  it("reextractAllDocuments returns the enqueued jobs", async () => {
    const { client } = clientReturning({ data: { reextractDocuments: [{ id: "j2" }, { id: "j3" }] } });
    const jobs = await reextractAllDocuments(client, true);
    expect(jobs.map((j) => j.id)).toEqual(["j2", "j3"]);
  });
});

describe("documents module", () => {
  it("listDocuments passes status/limit and unwraps documents", async () => {
    const { client, fetchImpl } = clientReturning({ data: { documents: [{ id: "d1", title: "A" }] } });
    const docs = await listDocuments(client, { limit: 25, status: "failed" });
    expect(docs[0].title).toBe("A");
    const body = JSON.parse(fetchImpl.mock.calls[0][1].body!);
    expect(body.variables).toMatchObject({ limit: 25, status: "failed" });
  });
});

describe("audit module", () => {
  it("listAuditEvents forwards filters and unwraps auditEvents", async () => {
    const { client, fetchImpl } = clientReturning({
      data: { auditEvents: [{ id: "a1", action: "graphql.document.read", outcome: "allowed" }] },
    });
    const events = await listAuditEvents(client, { action: "graphql.document.read", limit: 50 });
    expect(events[0].action).toBe("graphql.document.read");
    const body = JSON.parse(fetchImpl.mock.calls[0][1].body!);
    expect(body.variables).toMatchObject({ action: "graphql.document.read", limit: 50 });
  });
});

describe("backups module", () => {
  it("createBackup calls the admin mutation and returns the job", async () => {
    const { client, fetchImpl } = clientReturning({ data: { createBackup: { id: "job-b" } } });
    const job = await createBackup(client, "nightly");
    expect(job.id).toBe("job-b");
    const body = JSON.parse(fetchImpl.mock.calls[0][1].body!);
    expect(body.query).toContain("createBackup");
    expect(body.variables).toEqual({ note: "nightly" });
  });

  it("restoreBackup calls the admin mutation with the backup id", async () => {
    const { client, fetchImpl } = clientReturning({ data: { restoreBackup: { id: "job-r" } } });
    const job = await restoreBackup(client, "backup-123");
    expect(job.id).toBe("job-r");
    const body = JSON.parse(fetchImpl.mock.calls[0][1].body!);
    expect(body.variables).toEqual({ backupId: "backup-123" });
  });
});
