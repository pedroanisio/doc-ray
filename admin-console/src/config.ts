/**
 * Runtime config. The console is served same-origin behind nginx, so the
 * default base URL is "" (relative). Override with VITE_DOCRAY_BASE_URL only for
 * split deployments where the proxy lives elsewhere.
 */
export const BASE_URL: string = (import.meta.env?.VITE_DOCRAY_BASE_URL as string) ?? "";
