/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The console is served as static files behind an nginx front door that
// reverse-proxies the API/webapp on the SAME origin (see nginx.conf). That is
// deliberate: the doc-ray API ships NO CORS middleware, so a cross-origin SPA
// would be blocked. Same-origin proxying sidesteps CORS entirely.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Local dev proxy mirrors the production nginx routes so the dev server and
    // the container behave identically against a running doc-ray stack.
    proxy: {
      "/graphql": { target: "http://localhost:8000", changeOrigin: true },
      "/auth": { target: "http://localhost:8000", changeOrigin: true },
      "/health": { target: "http://localhost:8000", changeOrigin: true },
      "/readyz": { target: "http://localhost:8000", changeOrigin: true },
      "/metrics": { target: "http://localhost:8000", changeOrigin: true },
      "/operator": { target: "http://localhost:5001", changeOrigin: true },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.test.{ts,tsx}", "src/test/**", "src/main.tsx"],
    },
  },
});
