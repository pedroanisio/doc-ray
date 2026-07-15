/**
 * TokenStore — the single source of truth for the operator's JWT session.
 *
 * The doc-ray API issues an access/refresh JWT pair from POST /auth/login,
 * /auth/token and /auth/refresh (see auth_routes.py). Access tokens are
 * short-lived (DOCRAY_API_JWT_TTL_SECONDS, default 900s); refresh tokens rotate
 * on every use. This store persists the current bundle and answers the two
 * questions the client layer needs: "is the access token still good?" and
 * "can I refresh?".
 *
 * Persistence is injected (defaults to localStorage in the app) so tests can
 * pass any Storage-shaped backing and control expiry with an explicit clock.
 */

export interface TokenBundle {
  accessToken: string;
  refreshToken: string | null;
  tokenType: string;
  /** Epoch milliseconds at which the access token hard-expires. */
  expiresAt: number;
  /** Epoch milliseconds at which the refresh token hard-expires. */
  refreshExpiresAt: number;
}

type Listener = (bundle: TokenBundle | null) => void;

const STORAGE_KEY = "docray.admin.tokens";
// Refresh a little before the server-side hard expiry so an in-flight request
// never races the clock. 60s matches the smallest sane access TTL we support.
const EXPIRY_SKEW_MS = 60_000;

export class TokenStore {
  private readonly storage: Storage;
  private readonly listeners = new Set<Listener>();

  constructor(storage: Storage) {
    this.storage = storage;
  }

  read(): TokenBundle | null {
    const raw = this.storage.getItem(STORAGE_KEY);
    if (!raw) return null;
    try {
      const parsed = JSON.parse(raw) as TokenBundle;
      if (typeof parsed?.accessToken !== "string") return null;
      return parsed;
    } catch {
      // Corrupt payload (manual tampering, partial write): fail closed to
      // "logged out" rather than throwing into every caller.
      return null;
    }
  }

  accessToken(): string | null {
    return this.read()?.accessToken ?? null;
  }

  accessScopes(): string[] {
    const token = this.accessToken();
    if (!token) return [];
    const parts = token.split(".");
    if (parts.length !== 3 || !parts[1]) return [];
    try {
      const payload = JSON.parse(decodeBase64Url(parts[1])) as {
        scopes?: unknown;
        scope?: unknown;
      };
      if (Array.isArray(payload.scopes)) {
        return payload.scopes.filter((scope): scope is string => typeof scope === "string");
      }
      if (typeof payload.scope === "string") {
        return payload.scope.split(/\s+/u).filter(Boolean);
      }
      return [];
    } catch {
      return [];
    }
  }

  hasScope(scope: string): boolean {
    return this.accessScopes().includes(scope);
  }

  save(bundle: TokenBundle): void {
    this.storage.setItem(STORAGE_KEY, JSON.stringify(bundle));
    this.emit(bundle);
  }

  clear(): void {
    this.storage.removeItem(STORAGE_KEY);
    this.emit(null);
  }

  /** True when the access token is missing or within the skew of expiry. */
  isAccessExpired(nowMs: number): boolean {
    const bundle = this.read();
    if (!bundle) return true;
    return nowMs >= bundle.expiresAt - EXPIRY_SKEW_MS;
  }

  /** True when a non-null refresh token exists and has not hard-expired. */
  canRefresh(nowMs: number): boolean {
    const bundle = this.read();
    if (!bundle?.refreshToken) return false;
    return nowMs < bundle.refreshExpiresAt;
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private emit(bundle: TokenBundle | null): void {
    for (const listener of this.listeners) listener(bundle);
  }
}

function decodeBase64Url(value: string): string {
  const padded = value.replace(/-/gu, "+").replace(/_/gu, "/").padEnd(
    Math.ceil(value.length / 4) * 4,
    "=",
  );
  return atob(padded);
}
