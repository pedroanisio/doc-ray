import { describe, it, expect, beforeEach } from "vitest";
import { TokenStore, type TokenBundle } from "./tokenStore";

const bundle: TokenBundle = {
  accessToken: "access-abc",
  refreshToken: "refresh-xyz",
  tokenType: "Bearer",
  // 900s from an arbitrary fixed "now" the tests control explicitly.
  expiresAt: 1_000_900_000,
  refreshExpiresAt: 1_000_986_400,
};

function fakeAccessToken(payload: Record<string, unknown>): string {
  const encode = (value: unknown) =>
    btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/u, "");
  return `${encode({ alg: "none", typ: "JWT" })}.${encode(payload)}.sig`;
}

describe("TokenStore", () => {
  beforeEach(() => localStorage.clear());

  it("returns null when nothing is stored", () => {
    const store = new TokenStore(localStorage);
    expect(store.read()).toBeNull();
    expect(store.accessToken()).toBeNull();
  });

  it("persists and reads back a bundle across instances", () => {
    new TokenStore(localStorage).save(bundle);
    // A fresh instance must recover state from the backing storage.
    expect(new TokenStore(localStorage).read()).toEqual(bundle);
  });

  it("clears stored tokens", () => {
    const store = new TokenStore(localStorage);
    store.save(bundle);
    store.clear();
    expect(store.read()).toBeNull();
  });

  it("reports expiry with a safety skew so refresh happens before hard expiry", () => {
    const store = new TokenStore(localStorage);
    store.save(bundle);
    // 30s before expiry, still inside the 60s skew window => treated as expired.
    expect(store.isAccessExpired(bundle.expiresAt - 30_000)).toBe(true);
    // Well before expiry => not expired.
    expect(store.isAccessExpired(bundle.expiresAt - 120_000)).toBe(false);
  });

  it("treats a missing refresh token as unusable", () => {
    const store = new TokenStore(localStorage);
    store.save({ ...bundle, refreshToken: null });
    expect(store.canRefresh(0)).toBe(false);
  });

  it("treats an expired refresh token as unusable", () => {
    const store = new TokenStore(localStorage);
    store.save(bundle);
    expect(store.canRefresh(bundle.refreshExpiresAt + 1)).toBe(false);
    expect(store.canRefresh(bundle.refreshExpiresAt - 1_000)).toBe(true);
  });

  it("ignores corrupt storage payloads instead of throwing", () => {
    localStorage.setItem("docray.admin.tokens", "{not json");
    expect(new TokenStore(localStorage).read()).toBeNull();
  });

  it("notifies subscribers on save and clear", () => {
    const store = new TokenStore(localStorage);
    const seen: (TokenBundle | null)[] = [];
    store.subscribe((b) => seen.push(b));
    store.save(bundle);
    store.clear();
    expect(seen).toEqual([bundle, null]);
  });

  it("extracts scopes from locally issued access JWTs", () => {
    const store = new TokenStore(localStorage);
    store.save({ ...bundle, accessToken: fakeAccessToken({ scopes: ["read", "write"] }) });
    expect(store.accessScopes()).toEqual(["read", "write"]);
    expect(store.hasScope("write")).toBe(true);
  });

  it("accepts OAuth-style space-delimited scope claims", () => {
    const store = new TokenStore(localStorage);
    store.save({ ...bundle, accessToken: fakeAccessToken({ scope: "read write" }) });
    expect(store.accessScopes()).toEqual(["read", "write"]);
  });

  it("treats malformed access tokens as having no scopes", () => {
    const store = new TokenStore(localStorage);
    store.save({ ...bundle, accessToken: "not-a-jwt" });
    expect(store.accessScopes()).toEqual([]);
    expect(store.hasScope("write")).toBe(false);
  });
});
