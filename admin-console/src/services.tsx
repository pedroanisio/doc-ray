/**
 * Dependency wiring for the whole app, exposed through React context so tests
 * can inject fakes and production can inject the real, network-backed services.
 */
import { createContext, useContext, type ReactNode } from "react";
import { ApiClient } from "./api/client";
import { AuthService } from "./auth/authService";
import { TokenStore } from "./auth/tokenStore";
import { UnavailableIdentityProvider, type IdentityProvider } from "./api/identity";
import { BASE_URL } from "./config";

export interface Services {
  auth: AuthService;
  identity: IdentityProvider;
}

const ServicesContext = createContext<Services | null>(null);

export function ServicesProvider({
  services,
  children,
}: {
  services: Services;
  children: ReactNode;
}) {
  return <ServicesContext.Provider value={services}>{children}</ServicesContext.Provider>;
}

export function useServices(): Services {
  const ctx = useContext(ServicesContext);
  if (!ctx) throw new Error("useServices must be used within a ServicesProvider");
  return ctx;
}

/** Convenience accessor for the shared, auth-injected ApiClient. */
export function useApiClient(): ApiClient {
  return useServices().auth.client;
}

/** Build the production services against the live backend (same origin). */
export function createDefaultServices(): Services {
  const store = new TokenStore(window.localStorage);
  const auth = new AuthService({
    store,
    now: () => Date.now(),
    makeClient: (refresh) =>
      new ApiClient({
        baseUrl: BASE_URL,
        fetchImpl: (...args) => window.fetch(...args),
        getToken: () => store.accessToken(),
        refresh,
      }),
  });
  return { auth, identity: new UnavailableIdentityProvider() };
}
