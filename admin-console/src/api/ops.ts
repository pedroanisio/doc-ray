/**
 * Operational status reads.
 *   • GET /health          — liveness (api/health.py)
 *   • GET /readyz          — readiness; 503 until Postgres + object store answer
 *   • GET /operator/health.json — the webapp operator snapshot (operator_health.py),
 *     token-gated by DOCRAY_OPERATOR_HEALTH_TOKEN behind the proxy.
 */
import { ApiClient, ApiError } from "./client";
import type { LivenessProbe, OperatorHealth } from "./types";

export async function fetchLiveness(client: ApiClient): Promise<LivenessProbe> {
  return client.rest<LivenessProbe>("/health");
}

export interface Readiness {
  ready: boolean;
  detail?: LivenessProbe;
}

export async function fetchReadiness(client: ApiClient): Promise<Readiness> {
  try {
    const detail = await client.rest<LivenessProbe>("/readyz");
    return { ready: true, detail };
  } catch (err) {
    // /readyz answers 503 (not an auth failure) while a dependency is down.
    // That is a first-class "not ready" signal, not an exception to bubble.
    if (err instanceof ApiError && err.status === 503) return { ready: false };
    throw err;
  }
}

export async function fetchOperatorHealth(client: ApiClient): Promise<OperatorHealth> {
  return client.rest<OperatorHealth>("/operator/health.json");
}
