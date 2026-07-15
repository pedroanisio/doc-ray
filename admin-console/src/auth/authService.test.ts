import { describe, it, expect, beforeEach } from "vitest";
import { AuthService } from "./authService";
import { TokenStore } from "./tokenStore";
import { ApiClient } from "../api/client";
import { jsonResponse, makeFetchMock } from "../test/http";

const NOW = 1_000_000_000_000;

const pair = {
  access_token: "acc",
  refresh_token: "ref",
  token_type: "Bearer",
  expires_in: 900,
  refresh_expires_in: 86_400,
};

function wire(fetchImpl: typeof fetch) {
  const store = new TokenStore(localStorage);
  // The client's refresh hook is bound to the service after construction.
  const service = new AuthService({
    store,
    now: () => NOW,
    makeClient: (refresh) =>
      new ApiClient({
        baseUrl: "",
        fetchImpl,
        getToken: () => store.accessToken(),
        refresh,
      }),
  });
  return { store, service };
}

describe("AuthService.login", () => {
  beforeEach(() => localStorage.clear());

  it("POSTs credentials to /auth/login and stores the derived bundle", async () => {
    const fetchImpl = makeFetchMock(async () => jsonResponse(pair));
    const { store, service } = wire(fetchImpl);

    await service.login("ops@example.com", "hunter2");

    expect(fetchImpl.mock.calls[0][0]).toBe("/auth/login");
    const stored = store.read();
    expect(stored?.accessToken).toBe("acc");
    expect(stored?.refreshToken).toBe("ref");
    // expiresAt is derived from the injected clock + expires_in seconds.
    expect(stored?.expiresAt).toBe(NOW + 900_000);
    expect(stored?.refreshExpiresAt).toBe(NOW + 86_400_000);
  });

  it("does not send an Authorization header on login", async () => {
    const fetchImpl = makeFetchMock(async () => jsonResponse(pair));
    const { service } = wire(fetchImpl);
    localStorage.setItem(
      "docray.admin.tokens",
      JSON.stringify({ accessToken: "stale", refreshToken: "x", tokenType: "Bearer", expiresAt: 0, refreshExpiresAt: 0 }),
    );
    await service.login("a@b.c", "pw");
    expect(fetchImpl.mock.calls[0][1].headers).not.toHaveProperty("authorization");
  });

  it("propagates an AuthError on bad credentials (401)", async () => {
    const fetchImpl = makeFetchMock(async () => jsonResponse({ detail: "invalid" }, { status: 401 }));
    const { service } = wire(fetchImpl);
    await expect(service.login("a@b.c", "wrong")).rejects.toThrow();
  });
});

describe("AuthService.refresh", () => {
  beforeEach(() => localStorage.clear());

  it("returns false without hitting the network when there is no refresh token", async () => {
    const fetchImpl = makeFetchMock(async () => jsonResponse(pair));
    const { service } = wire(fetchImpl);
    expect(await service.refresh()).toBe(false);
    expect(fetchImpl.mock.calls).toHaveLength(0);
  });

  it("rotates and stores a new bundle on success", async () => {
    const store = new TokenStore(localStorage);
    store.save({ accessToken: "old", refreshToken: "ref", tokenType: "Bearer", expiresAt: NOW, refreshExpiresAt: NOW + 999_999 });
    const rotated = { ...pair, access_token: "new-acc", refresh_token: "new-ref" };
    const fetchImpl = makeFetchMock(async () => jsonResponse(rotated));
    const service = new AuthService({
      store,
      now: () => NOW,
      makeClient: (refresh) =>
        new ApiClient({ baseUrl: "", fetchImpl, getToken: () => store.accessToken(), refresh }),
    });

    const ok = await service.refresh();

    expect(ok).toBe(true);
    expect(fetchImpl.mock.calls[0][0]).toBe("/auth/refresh");
    expect(store.read()?.accessToken).toBe("new-acc");
    expect(store.read()?.refreshToken).toBe("new-ref");
  });

  it("clears the session and returns false when the refresh token is rejected", async () => {
    const store = new TokenStore(localStorage);
    store.save({ accessToken: "old", refreshToken: "ref", tokenType: "Bearer", expiresAt: NOW, refreshExpiresAt: NOW + 999_999 });
    const fetchImpl = makeFetchMock(async () => jsonResponse({ detail: "Invalid refresh token" }, { status: 401 }));
    const service = new AuthService({
      store,
      now: () => NOW,
      makeClient: (refresh) =>
        new ApiClient({ baseUrl: "", fetchImpl, getToken: () => store.accessToken(), refresh }),
    });

    expect(await service.refresh()).toBe(false);
    expect(store.read()).toBeNull();
  });
});

describe("AuthService.logout", () => {
  it("clears local tokens (the API exposes no logout endpoint)", () => {
    const { store, service } = wire(makeFetchMock(async () => jsonResponse({})));
    store.save({ accessToken: "a", refreshToken: "r", tokenType: "Bearer", expiresAt: NOW, refreshExpiresAt: NOW });
    service.logout();
    expect(store.read()).toBeNull();
  });
});

describe("AuthService.isAuthenticated", () => {
  it("is false with no tokens and true with a live access token", () => {
    const { store, service } = wire(makeFetchMock(async () => jsonResponse({})));
    expect(service.isAuthenticated()).toBe(false);
    store.save({ accessToken: "a", refreshToken: "r", tokenType: "Bearer", expiresAt: NOW + 10_000_000, refreshExpiresAt: NOW + 10_000_000 });
    expect(service.isAuthenticated()).toBe(true);
  });
});
