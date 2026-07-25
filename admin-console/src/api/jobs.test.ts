import { describe, it, expect, vi } from "vitest";
import { listJobs } from "./jobs";
import type { ApiClient } from "./client";

function fakeClient(jobs: unknown[]) {
  const graphql = vi.fn().mockResolvedValue({ jobs });
  return { client: { graphql } as unknown as ApiClient, graphql };
}

describe("listJobs", () => {
  it("forwards status, kind, limit and after as GraphQL variables", async () => {
    const { client, graphql } = fakeClient([]);
    await listJobs(client, { status: "failed", kind: "annotate", limit: 25, after: "cur-1" });
    const [, vars] = graphql.mock.calls[0];
    expect(vars).toMatchObject({
      status: "failed",
      kind: "annotate",
      limit: 25,
      after: "cur-1",
    });
  });

  it("sends null for omitted filters (not undefined) so the server sees no filter", async () => {
    const { client, graphql } = fakeClient([]);
    await listJobs(client, { limit: 10 });
    const [, vars] = graphql.mock.calls[0];
    expect(vars.status).toBeNull();
    expect(vars.kind).toBeNull();
    expect(vars.after).toBeNull();
  });

  it("requests the cursor field so the page can paginate", async () => {
    const { client, graphql } = fakeClient([]);
    await listJobs(client, {});
    const [query] = graphql.mock.calls[0];
    expect(query).toContain("cursor");
    expect(query).toContain("$status: String");
    expect(query).toContain("$kind: String");
  });

  it("returns the jobs array from the response", async () => {
    const rows = [{ id: "1", kind: "ingest", status: "succeeded", cursor: "1" }];
    const { client } = fakeClient(rows);
    const got = await listJobs(client, {});
    expect(got).toEqual(rows);
  });
});
