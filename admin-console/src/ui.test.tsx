import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { ServicesProvider, type Services } from "./services";
import { AuthProvider } from "./auth/AuthContext";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { LoginPage } from "./pages/LoginPage";
import { AuthService } from "./auth/authService";
import { TokenStore } from "./auth/tokenStore";
import { ApiClient } from "./api/client";
import { UnavailableIdentityProvider } from "./api/identity";
import { BillingPage } from "./pages/BillingPage";

function servicesWithFetch(fetchImpl: typeof fetch): Services {
  const store = new TokenStore(localStorage);
  const auth = new AuthService({
    store,
    now: () => Date.now(),
    makeClient: (refresh) =>
      new ApiClient({ baseUrl: "", fetchImpl, getToken: () => store.accessToken(), refresh }),
  });
  return { auth, identity: new UnavailableIdentityProvider() };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("ProtectedRoute", () => {
  it("redirects to /login when unauthenticated", () => {
    const services = servicesWithFetch(vi.fn() as unknown as typeof fetch);
    render(
      <ServicesProvider services={services}>
        <AuthProvider>
          <MemoryRouter initialEntries={["/secret"]}>
            <Routes>
              <Route path="/login" element={<div>please sign in</div>} />
              <Route
                path="/secret"
                element={
                  <ProtectedRoute>
                    <div>classified</div>
                  </ProtectedRoute>
                }
              />
            </Routes>
          </MemoryRouter>
        </AuthProvider>
      </ServicesProvider>,
    );
    expect(screen.getByText("please sign in")).toBeInTheDocument();
    expect(screen.queryByText("classified")).not.toBeInTheDocument();
  });

  it("renders the protected content when a live token exists", () => {
    const services = servicesWithFetch(vi.fn() as unknown as typeof fetch);
    services.auth.store.save({
      accessToken: "a",
      refreshToken: "r",
      tokenType: "Bearer",
      expiresAt: Date.now() + 10_000_000,
      refreshExpiresAt: Date.now() + 10_000_000,
    });
    render(
      <ServicesProvider services={services}>
        <AuthProvider>
          <MemoryRouter initialEntries={["/secret"]}>
            <Routes>
              <Route path="/login" element={<div>please sign in</div>} />
              <Route
                path="/secret"
                element={
                  <ProtectedRoute>
                    <div>classified</div>
                  </ProtectedRoute>
                }
              />
            </Routes>
          </MemoryRouter>
        </AuthProvider>
      </ServicesProvider>,
    );
    expect(screen.getByText("classified")).toBeInTheDocument();
  });
});

describe("LoginPage", () => {
  it("signs in with entered credentials and establishes a session", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse({
        access_token: "acc",
        refresh_token: "ref",
        token_type: "Bearer",
        expires_in: 900,
        refresh_expires_in: 86_400,
      }),
    ) as unknown as typeof fetch;
    const services = servicesWithFetch(fetchImpl);

    render(
      <ServicesProvider services={services}>
        <AuthProvider>
          <MemoryRouter>
            <LoginPage />
          </MemoryRouter>
        </AuthProvider>
      </ServicesProvider>,
    );

    await userEvent.type(screen.getByLabelText(/email/i), "ops@example.com");
    await userEvent.type(screen.getByLabelText(/password/i), "hunter2");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(services.auth.isAuthenticated()).toBe(true));
    const call = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(call[0]).toBe("/auth/login");
  });

  it("surfaces an error message when login fails", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({ detail: "invalid" }, 401)) as unknown as typeof fetch;
    const services = servicesWithFetch(fetchImpl);

    render(
      <ServicesProvider services={services}>
        <AuthProvider>
          <MemoryRouter>
            <LoginPage />
          </MemoryRouter>
        </AuthProvider>
      </ServicesProvider>,
    );

    await userEvent.type(screen.getByLabelText(/email/i), "ops@example.com");
    await userEvent.type(screen.getByLabelText(/password/i), "wrong");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(services.auth.isAuthenticated()).toBe(false);
  });
});

describe("BillingPage", () => {
  it("renders the live billing status and payment actions", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse({
        data: {
          billingStatus: {
            tenantId: "t1",
            tenantSlug: "default",
            tenantName: "Current tenant",
            stripeCustomerId: "cus_123",
            billingEmail: "ops@example.com",
            stripeSubscriptionId: "sub_123",
            stripePriceId: "price_123",
            subscriptionStatus: "active",
            currentPeriodEnd: "2026-08-01T00:00:00Z",
            cancelAtPeriodEnd: false,
            latestInvoiceId: "in_123",
            checkoutSessionId: "cs_123",
            subscriptionUpdatedAt: "2026-07-15T00:00:00Z",
            checkoutReady: true,
            portalReady: true,
            enforcementEnabled: true,
            entitled: true,
            entitlementReason: "subscription is active",
          },
        },
      }),
    ) as unknown as typeof fetch;
    const services = servicesWithFetch(fetchImpl);

    render(
      <ServicesProvider services={services}>
        <MemoryRouter>
          <BillingPage />
        </MemoryRouter>
      </ServicesProvider>,
    );

    expect(await screen.findByText("Current tenant")).toBeInTheDocument();
    expect(screen.getByText("entitled")).toBeInTheDocument();
    expect(screen.getAllByText("subscription is active")).toHaveLength(2);
    expect(screen.getByRole("button", { name: /start subscription/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /manage payment method/i })).toBeEnabled();
  });
});
