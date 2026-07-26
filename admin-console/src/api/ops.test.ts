import { describe, it, expect } from "vitest";
import { ApiClient, ApiError } from "./client";
import { makeFetchMock } from "../test/http";
import { fetchOperatorHealth } from "./ops";

function makeClient(fetchImpl: typeof fetch) {
  return new ApiClient({
    baseUrl: "",
    fetchImpl,
    getToken: () => null,
    refresh: async () => false,
  });
}

describe("fetchOperatorHealth", () => {
  it("maps a 404 to an authorization/token message (the real token-missing case)", async () => {
    // require_operator_health() aborts 404 when no token/session is present. THIS
    // is the case where the token hint is accurate — and only this case (#22).
    const client = makeClient(
      makeFetchMock(
        async () => new Response("Not Found", { status: 404, headers: { "content-type": "text/html" } }),
      ),
    );

    const err = await fetchOperatorHealth(client).catch((e: unknown) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(404);
    expect((err as ApiError).message).toMatch(/operator/i);
    expect((err as ApiError).message).toMatch(/DOCRAY_OPERATOR_HEALTH_TOKEN/);
  });

  it("passes a routing/parse error through unchanged (HTML body on a 200)", async () => {
    // The screenshot's actual cause: SPA shell served with 200. The message must
    // point at routing, and must NOT be relabeled as a token problem.
    const client = makeClient(
      makeFetchMock(
        async () =>
          new Response("<!doctype html><html></html>", {
            status: 200,
            headers: { "content-type": "text/html; charset=utf-8" },
          }),
      ),
    );

    const err = await fetchOperatorHealth(client).catch((e: unknown) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(200);
    expect((err as ApiError).message).toMatch(/reverse-proxied|SPA shell|expected JSON/i);
    expect((err as ApiError).message).not.toMatch(/DOCRAY_OPERATOR_HEALTH_TOKEN/);
  });

  it("returns the parsed operator-health.v1 payload on success", async () => {
    const client = makeClient(
      makeFetchMock(
        async () =>
          new Response(
            JSON.stringify({ schema_version: "operator-health.v1", corpus: { totals: {} }, database: {} }),
            { status: 200, headers: { "content-type": "application/json" } },
          ),
      ),
    );

    const health = await fetchOperatorHealth(client);

    expect(health.schema_version).toBe("operator-health.v1");
  });
});
