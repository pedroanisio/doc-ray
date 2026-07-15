/**
 * AuthService — owns the operator's session lifecycle against the doc-ray
 * REST auth surface (auth_routes.py):
 *   • login   → POST /auth/login   { email, password }        (auth_local_enabled)
 *   • refresh → POST /auth/refresh { refresh_token }          (rotating)
 *   • token   → POST /auth/token   (exchange an existing credential)
 *
 * There is deliberately NO logout() network call: the API exposes no
 * /auth/logout route (confirmed against auth_routes.py). Logout is therefore a
 * local token wipe; server-side invalidation happens through refresh-token
 * rotation/reuse-detection on the next refresh.
 *
 * The clock (`now`) and the client factory are injected so the whole flow is
 * deterministic under test.
 */
import { ApiClient, ApiError } from "../api/client";
import { TokenStore, type TokenBundle } from "./tokenStore";
import type { JwtPairResponse } from "../api/types";

export interface AuthServiceOptions {
  store: TokenStore;
  now: () => number;
  /** Build the ApiClient, wiring our refresh() in as its 401 recovery hook. */
  makeClient: (refresh: () => Promise<boolean>) => ApiClient;
}

export class AuthService {
  readonly store: TokenStore;
  readonly client: ApiClient;
  private readonly now: () => number;

  constructor(opts: AuthServiceOptions) {
    this.store = opts.store;
    this.now = opts.now;
    // Bind refresh as a stable arrow so the client can call it on any 401.
    this.client = opts.makeClient(() => this.refresh());
  }

  isAuthenticated(): boolean {
    return !this.store.isAccessExpired(this.now());
  }

  hasScope(scope: string): boolean {
    return this.store.hasScope(scope);
  }

  async login(email: string, password: string): Promise<void> {
    const pair = await this.client.postJson<JwtPairResponse>(
      "/auth/login",
      { email, password },
      /* withAuth */ false,
    );
    this.store.save(this.toBundle(pair));
  }

  /** Exchange an already-authenticated request (static token/OIDC) for a JWT. */
  async exchangeToken(): Promise<void> {
    const pair = await this.client.postJson<JwtPairResponse>("/auth/token", {}, /* withAuth */ true);
    this.store.save(this.toBundle(pair));
  }

  async refresh(): Promise<boolean> {
    const now = this.now();
    if (!this.store.canRefresh(now)) {
      // No usable refresh token — nothing to do, and do not touch the network.
      return false;
    }
    const refreshToken = this.store.read()!.refreshToken!;
    try {
      const pair = await this.client.postJson<JwtPairResponse>(
        "/auth/refresh",
        { refresh_token: refreshToken },
        /* withAuth */ false,
      );
      this.store.save(this.toBundle(pair));
      return true;
    } catch (err) {
      // A rejected/expired refresh token is terminal: drop the session so the
      // UI routes to /login rather than looping on a dead token.
      if (err instanceof ApiError) {
        this.store.clear();
        return false;
      }
      throw err;
    }
  }

  logout(): void {
    this.store.clear();
  }

  private toBundle(pair: JwtPairResponse): TokenBundle {
    const now = this.now();
    return {
      accessToken: pair.access_token,
      refreshToken: pair.refresh_token ?? null,
      tokenType: pair.token_type,
      expiresAt: now + pair.expires_in * 1000,
      refreshExpiresAt: now + pair.refresh_expires_in * 1000,
    };
  }
}
