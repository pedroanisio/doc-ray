import { describe, it, expect } from "vitest";
import { ApiClient, AuthError } from "./client";
import { jsonResponse, makeFetchMock } from "../test/http";

function makeClient(
  fetchImpl: typeof fetch,
  opts: Partial<{ token: () => string | null; refresh: () => Promise<boolean> }> = {},
) {
  return new ApiClient({
    baseUrl: "",
    fetchImpl,
    getToken: opts.token ?? (() => null),
    refresh: opts.refresh ?? (async () => false),
  });
}

describe("ApiClient.graphql", () => {
  it("POSTs to /graphql with the query and variables and returns data", async () => {
    const fetchImpl = makeFetchMock(async () => jsonResponse({ data: { version: "1.2.3" } }));
    const client = makeClient(fetchImpl);

    const data = await client.graphql<{ version: string }>("query { version }", { a: 1 });

    expect(data).toEqual({ version: "1.2.3" });
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("/graphql");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body!)).toEqual({ query: "query { version }", variables: { a: 1 } });
    expect(init.headers["content-type"]).toBe("application/json");
  });

  it("injects a Bearer token when one is available", async () => {
    const fetchImpl = makeFetchMock(async () => jsonResponse({ data: { ok: true } }));
    const client = makeClient(fetchImpl, { token: () => "abc.def.ghi" });

    await client.graphql("query { ok }");

    expect(fetchImpl.mock.calls[0][1].headers["authorization"]).toBe("Bearer abc.def.ghi");
  });

  it("omits the Authorization header when there is no token", async () => {
    const fetchImpl = makeFetchMock(async () => jsonResponse({ data: {} }));
    const client = makeClient(fetchImpl);
    await client.graphql("query { ok }");
    expect(fetchImpl.mock.calls[0][1].headers["authorization"]).toBeUndefined();
  });

  it("raises ApiError carrying the GraphQL errors array", async () => {
    const fetchImpl = makeFetchMock(async () =>
      jsonResponse({ errors: [{ message: "this mutation requires an admin principal" }] }),
    );
    const client = makeClient(fetchImpl);

    await expect(client.graphql("mutation { createBackup { id } }")).rejects.toMatchObject({
      name: "ApiError",
      message: expect.stringContaining("admin principal"),
    });
  });
});

describe("ApiClient auth handling", () => {
  it("raises AuthError on a 401 when refresh is impossible", async () => {
    const fetchImpl = makeFetchMock(async () => jsonResponse({}, { status: 401 }));
    const client = makeClient(fetchImpl, { refresh: async () => false });

    await expect(client.rest("/operator/health.json")).rejects.toBeInstanceOf(AuthError);
  });

  it("refreshes once and retries the request on a 401", async () => {
    let calls = 0;
    const fetchImpl = makeFetchMock(async () => {
      calls += 1;
      return calls === 1 ? jsonResponse({}, { status: 401 }) : jsonResponse({ status: "ok" });
    });
    let refreshCalls = 0;
    const refresh = async () => {
      refreshCalls += 1;
      return true;
    };
    const client = makeClient(fetchImpl, { refresh });

    const data = await client.rest<{ status: string }>("/health");

    expect(refreshCalls).toBe(1);
    expect(data).toEqual({ status: "ok" });
    expect(fetchImpl.mock.calls).toHaveLength(2);
  });

  it("does not retry more than once even if refresh keeps succeeding", async () => {
    const fetchImpl = makeFetchMock(async () => jsonResponse({}, { status: 401 }));
    let refreshCalls = 0;
    const refresh = async () => {
      refreshCalls += 1;
      return true;
    };
    const client = makeClient(fetchImpl, { refresh });

    await expect(client.rest("/health")).rejects.toBeInstanceOf(AuthError);
    expect(refreshCalls).toBe(1);
    expect(fetchImpl.mock.calls).toHaveLength(2);
  });
});

describe("ApiClient.rest", () => {
  it("prefixes the configured base URL and parses JSON", async () => {
    const fetchImpl = makeFetchMock(async () => jsonResponse({ status: "ok", version: "9" }));
    const client = new ApiClient({
      baseUrl: "https://ops.example.com",
      fetchImpl,
      getToken: () => null,
      refresh: async () => false,
    });

    await client.rest("/health");
    expect(fetchImpl.mock.calls[0][0]).toBe("https://ops.example.com/health");
  });

  it("raises ApiError with the status on a 500", async () => {
    const fetchImpl = makeFetchMock(async () => new Response("boom", { status: 500 }));
    const client = makeClient(fetchImpl);
    await expect(client.rest("/health")).rejects.toMatchObject({ name: "ApiError", status: 500 });
  });
});
