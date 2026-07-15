/**
 * ApiClient — the single typed transport for the console.
 *
 * It speaks two protocols against the doc-ray backend, both on the SAME origin
 * (nginx reverse-proxies them — see nginx.conf):
 *   • GraphQL at POST /graphql   (reads + admin mutations)
 *   • REST for /auth/*, /health, /readyz, /operator/health.json
 *
 * The doc-ray API authenticates via `Authorization: Bearer <jwt>` (or the
 * X-DocRay-API-Token header) — see security.py::_request_token. This client
 * injects the bearer token and, on a 401, attempts a single token refresh and
 * one retry before surfacing an AuthError so the UI can bounce to /login.
 *
 * fetch, token accessor, and refresh are all injected so the module is unit
 * testable with no network and no globals.
 */

export interface GraphQLError {
  message: string;
  path?: (string | number)[];
  extensions?: Record<string, unknown>;
}

export class ApiError extends Error {
  readonly status?: number;
  readonly graphQLErrors?: GraphQLError[];
  constructor(message: string, opts: { status?: number; graphQLErrors?: GraphQLError[] } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = opts.status;
    this.graphQLErrors = opts.graphQLErrors;
  }
}

/** Raised when the request is unauthenticated and cannot be recovered. */
export class AuthError extends ApiError {
  constructor(message = "authentication required") {
    super(message, { status: 401 });
    this.name = "AuthError";
  }
}

export interface ApiClientOptions {
  baseUrl: string;
  fetchImpl: typeof fetch;
  getToken: () => string | null;
  /** Attempt to refresh the access token. Returns true if a new token is ready. */
  refresh: () => Promise<boolean>;
}

interface GraphQLResponse<T> {
  data?: T;
  errors?: GraphQLError[];
}

export class ApiClient {
  private readonly opts: ApiClientOptions;

  constructor(opts: ApiClientOptions) {
    this.opts = opts;
  }

  async graphql<T>(query: string, variables: Record<string, unknown> = {}): Promise<T> {
    const res = await this.send("/graphql", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ query, variables }),
    });
    const payload = (await res.json()) as GraphQLResponse<T>;
    if (payload.errors?.length) {
      throw new ApiError(payload.errors.map((e) => e.message).join("; "), {
        graphQLErrors: payload.errors,
      });
    }
    if (payload.data === undefined) {
      throw new ApiError("GraphQL response contained no data");
    }
    return payload.data;
  }

  /** GET a JSON REST endpoint. */
  async rest<T>(path: string): Promise<T> {
    const res = await this.send(path, { method: "GET" });
    return (await res.json()) as T;
  }

  /** POST a JSON REST endpoint (used by the auth flow; auth passes no bearer). */
  async postJson<T>(path: string, body: unknown, withAuth = true): Promise<T> {
    const res = await this.send(
      path,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body ?? {}),
      },
      { withAuth },
    );
    return (await res.json()) as T;
  }

  /**
   * Core send with auth injection + single-shot refresh-and-retry on 401.
   * Any non-2xx that is not a recoverable 401 becomes an ApiError.
   */
  private async send(
    path: string,
    init: RequestInit & { headers?: Record<string, string> },
    cfg: { withAuth?: boolean } = {},
  ): Promise<Response> {
    const withAuth = cfg.withAuth ?? true;

    const attempt = async (): Promise<Response> => {
      const headers: Record<string, string> = { ...(init.headers ?? {}) };
      const token = withAuth ? this.opts.getToken() : null;
      if (token) headers["authorization"] = `Bearer ${token}`;
      return this.opts.fetchImpl(`${this.opts.baseUrl}${path}`, { ...init, headers });
    };

    let res = await attempt();
    if (res.status === 401 && withAuth) {
      const refreshed = await this.opts.refresh();
      if (refreshed) res = await attempt();
      if (res.status === 401) throw new AuthError();
    }
    if (!res.ok) {
      throw new ApiError(`request to ${path} failed`, { status: res.status });
    }
    return res;
  }
}
