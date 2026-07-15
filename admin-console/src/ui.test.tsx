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
  function fakeAccessToken(scopes: string[]) {
    const encode = (value: unknown) =>
      btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/u, "");
    return `${encode({ alg: "none", typ: "JWT" })}.${encode({ scopes })}.sig`;
  }

  function billingServices(fetchImpl: typeof fetch, scopes = ["read", "write"]) {
    const services = servicesWithFetch(fetchImpl);
    services.auth.store.save({
      accessToken: fakeAccessToken(scopes),
      refreshToken: "refresh",
      tokenType: "Bearer",
      expiresAt: Date.now() + 900_000,
      refreshExpiresAt: Date.now() + 86_400_000,
    });
    return services;
  }

  function billingFetch(statusOverrides: Record<string, unknown> = {}) {
    return vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body ?? "{}"));
      if (String(body.query).includes("createBillingCheckoutSession")) {
        return jsonResponse({
          data: { createBillingCheckoutSession: { url: "https://checkout.stripe.test/hosted" } },
        });
      }
      if (String(body.query).includes("createBillingPortalSession")) {
        return jsonResponse({
          data: { createBillingPortalSession: { url: "https://billing.stripe.test/hosted" } },
        });
      }
      if (String(body.query).includes("billingEvents")) {
        return jsonResponse({
          data: {
            billingEvents: [
              {
                stripeEventId: "evt_invoice_1",
                eventType: "invoice.paid",
                livemode: false,
                processedAt: "2026-07-15T10:00:00Z",
                createdAt: "2026-07-15T09:59:59Z",
                objectId: "in_123",
                customerId: "cus_123",
                subscriptionId: "sub_123",
                objectStatus: "paid",
                latestInvoiceId: null,
              },
            ],
          },
        });
      }
      return jsonResponse({
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
            webhookReady: true,
            webhookEndpointUrl: "https://app.example.com/stripe/webhook",
            liveModeReady: true,
            missingConfiguration: [],
            configurationIssues: [],
            requiredWebhookEvents: [
              "checkout.session.completed",
              "customer.subscription.updated",
              "invoice.paid",
              "invoice.payment_succeeded",
            ],
            enforcementEnabled: true,
            entitled: true,
            entitlementReason: "subscription is active",
            ...statusOverrides,
          },
        },
      });
    }) as unknown as typeof fetch;
  }

  it("renders the live billing status and payment actions", async () => {
    const fetchImpl = billingFetch();
    const services = billingServices(fetchImpl);

    render(
      <ServicesProvider services={services}>
        <MemoryRouter>
          <BillingPage />
        </MemoryRouter>
      </ServicesProvider>,
    );

    expect(await screen.findByText("Current tenant")).toBeInTheDocument();
    expect(screen.getByText("subscription active")).toBeInTheDocument();
    expect(screen.getByText("ready")).toBeInTheDocument();
    expect(screen.getByText("https://app.example.com/stripe/webhook")).toBeInTheDocument();
    expect(screen.getByText(/checkout.session.completed/)).toBeInTheDocument();
    expect(screen.getByText(/invoice.payment_succeeded/)).toBeInTheDocument();
    expect(screen.getAllByText("subscription is active")).toHaveLength(2);
    expect(await screen.findByText("invoice.paid")).toBeInTheDocument();
    expect(screen.getByText(/evt_invoice_1/)).toBeInTheDocument();
    expect(screen.getAllByRole("columnheader").map((header) => header.textContent)).toEqual([
      "Event",
      "Status",
      "Object",
      "Subscription",
      "Processed",
    ]);
    expect(screen.getByRole("button", { name: /subscription active/i })).toBeDisabled();
    expect(screen.getByText(/Use the Customer Portal/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /manage payment method/i })).toBeEnabled();
  });

  it("does not present disabled enforcement as a paid Stripe subscription", async () => {
    const fetchImpl = billingFetch({
      stripeCustomerId: null,
      stripeSubscriptionId: null,
      stripePriceId: null,
      subscriptionStatus: "none",
      currentPeriodEnd: null,
      latestInvoiceId: null,
      checkoutSessionId: null,
      liveModeReady: false,
      missingConfiguration: ["DOCRAY_BILLING_ENFORCEMENT_ENABLED=true"],
      enforcementEnabled: false,
      entitled: true,
      entitlementReason: "billing enforcement is disabled",
    });
    const services = billingServices(fetchImpl);

    render(
      <ServicesProvider services={services}>
        <MemoryRouter>
          <BillingPage />
        </MemoryRouter>
      </ServicesProvider>,
    );

    expect(await screen.findByText("Current tenant")).toBeInTheDocument();
    expect(screen.getByText("no subscription")).toBeInTheDocument();
    expect(screen.getByText("No Stripe subscription has been recorded")).toBeInTheDocument();
    expect(screen.getByText("Paid access")).toBeInTheDocument();
    expect(screen.getByText("allowed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /complete setup/i })).toBeDisabled();
  });

  it("surfaces unsafe live Stripe configuration issues", async () => {
    const fetchImpl = billingFetch({
      stripeCustomerId: "cus_unsafe_live",
      stripeSubscriptionId: null,
      stripePriceId: null,
      subscriptionStatus: "none",
      currentPeriodEnd: null,
      latestInvoiceId: null,
      checkoutSessionId: null,
      entitled: false,
      entitlementReason: "subscription is none",
      liveModeReady: false,
      configurationIssues: [
        "DOCRAY_STRIPE_SECRET_KEY uses a Stripe test-mode key",
        "DOCRAY_PUBLIC_BASE_URL must use https for live billing",
      ],
    });
    const services = billingServices(fetchImpl);

    render(
      <ServicesProvider services={services}>
        <MemoryRouter>
          <BillingPage />
        </MemoryRouter>
      </ServicesProvider>,
    );

    expect(await screen.findByText("needs setup")).toBeInTheDocument();
    expect(screen.getByText(/DOCRAY_STRIPE_SECRET_KEY uses a Stripe test-mode key/)).toBeInTheDocument();
    expect(screen.getByText(/DOCRAY_PUBLIC_BASE_URL must use https for live billing/)).toBeInTheDocument();
    expect(screen.getByText("Live-safe configuration")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /complete setup/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /manage payment method/i })).toBeDisabled();
  });

  it("keeps Checkout available when no live subscription exists", async () => {
    const fetchImpl = billingFetch({
      stripeCustomerId: null,
      stripeSubscriptionId: null,
      subscriptionStatus: "none",
      currentPeriodEnd: null,
      latestInvoiceId: null,
      checkoutSessionId: null,
      entitled: false,
      entitlementReason: "subscription is none",
    });
    const services = billingServices(fetchImpl);

    render(
      <ServicesProvider services={services}>
        <MemoryRouter>
          <BillingPage />
        </MemoryRouter>
      </ServicesProvider>,
    );

    expect(await screen.findByText("Current tenant")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /start subscription/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /manage payment method/i })).toBeDisabled();
  });

  it("lets read-scoped admins monitor Billing without payment mutation actions", async () => {
    const fetchImpl = billingFetch({
      stripeCustomerId: null,
      stripeSubscriptionId: null,
      subscriptionStatus: "none",
      currentPeriodEnd: null,
      latestInvoiceId: null,
      checkoutSessionId: null,
      entitled: false,
      entitlementReason: "subscription is none",
    });
    const services = billingServices(fetchImpl, ["read"]);

    render(
      <ServicesProvider services={services}>
        <MemoryRouter>
          <BillingPage />
        </MemoryRouter>
      </ServicesProvider>,
    );

    expect(await screen.findByText("Current tenant")).toBeInTheDocument();
    expect(screen.getByText("no subscription")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /read-only access/i })).toBeDisabled();
    expect(screen.getAllByText(/requires write access/i)).toHaveLength(2);
    expect(
      (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls.some(([, init]) =>
        String(init?.body ?? "").includes("createBillingCheckoutSession"),
      ),
    ).toBe(false);
  });

  it("routes checkout-completed tenants to Customer Portal instead of duplicate Checkout", async () => {
    const fetchImpl = billingFetch({
      subscriptionStatus: "checkout_completed",
      entitled: false,
      entitlementReason: "subscription is checkout_completed",
    });
    const services = billingServices(fetchImpl);

    render(
      <ServicesProvider services={services}>
        <MemoryRouter>
          <BillingPage />
        </MemoryRouter>
      </ServicesProvider>,
    );

    expect(await screen.findByText("Current tenant")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /use customer portal/i })).toBeDisabled();
    expect(screen.getByText(/complete recovery/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /manage payment method/i })).toBeEnabled();
  });

  it("blocks duplicate Checkout while a hosted session is pending", async () => {
    const fetchImpl = billingFetch({
      stripeSubscriptionId: null,
      subscriptionStatus: "checkout_pending",
      checkoutSessionId: "cs_pending_123",
      entitled: false,
      entitlementReason: "subscription is checkout_pending",
    });
    const services = billingServices(fetchImpl);

    render(
      <ServicesProvider services={services}>
        <MemoryRouter>
          <BillingPage />
        </MemoryRouter>
      </ServicesProvider>,
    );

    expect(await screen.findByText("Current tenant")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /checkout pending/i })).toBeDisabled();
    expect(screen.getByText(/complete recovery/)).toBeInTheDocument();
  });

  it("opens hosted Checkout from the Billing page action", async () => {
    const fetchImpl = billingFetch({
      stripeCustomerId: null,
      stripeSubscriptionId: null,
      subscriptionStatus: "none",
      currentPeriodEnd: null,
      latestInvoiceId: null,
      checkoutSessionId: null,
      entitled: false,
      entitlementReason: "subscription is none",
    });
    const services = billingServices(fetchImpl);
    const redirectToHostedUrl = vi.fn();

    render(
      <ServicesProvider services={services}>
        <MemoryRouter>
          <BillingPage redirectToHostedUrl={redirectToHostedUrl} />
        </MemoryRouter>
      </ServicesProvider>,
    );

    await userEvent.click(await screen.findByRole("button", { name: /start subscription/i }));

    await waitFor(() =>
      expect(redirectToHostedUrl).toHaveBeenCalledWith("https://checkout.stripe.test/hosted"),
    );
    const mutationCall = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls.find(
      ([, init]) => String(init?.body ?? "").includes("createBillingCheckoutSession"),
    );
    expect(mutationCall).toBeDefined();
    const body = JSON.parse(String(mutationCall?.[1]?.body ?? "{}"));
    expect(body.variables).toEqual({
      successUrl: `${window.location.origin}/billing?checkout=success`,
      cancelUrl: `${window.location.origin}/billing?checkout=cancel`,
    });
  });

  it("opens hosted Customer Portal from the Billing page action", async () => {
    const fetchImpl = billingFetch();
    const services = billingServices(fetchImpl);
    const redirectToHostedUrl = vi.fn();

    render(
      <ServicesProvider services={services}>
        <MemoryRouter>
          <BillingPage redirectToHostedUrl={redirectToHostedUrl} />
        </MemoryRouter>
      </ServicesProvider>,
    );

    await userEvent.click(await screen.findByRole("button", { name: /manage payment method/i }));

    await waitFor(() =>
      expect(redirectToHostedUrl).toHaveBeenCalledWith("https://billing.stripe.test/hosted"),
    );
    const mutationCall = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls.find(
      ([, init]) => String(init?.body ?? "").includes("createBillingPortalSession"),
    );
    expect(mutationCall).toBeDefined();
    const body = JSON.parse(String(mutationCall?.[1]?.body ?? "{}"));
    expect(body.variables).toEqual({
      returnUrl: `${window.location.origin}/billing`,
    });
  });

  it("refreshes billing status from the page action", async () => {
    const fetchImpl = billingFetch();
    const services = billingServices(fetchImpl);

    render(
      <ServicesProvider services={services}>
        <MemoryRouter>
          <BillingPage />
        </MemoryRouter>
      </ServicesProvider>,
    );

    const refresh = await screen.findByRole("button", { name: /refresh billing status/i });
    await waitFor(() =>
      expect((fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls).toHaveLength(2),
    );

    await userEvent.click(refresh);

    await waitFor(() =>
      expect((fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls).toHaveLength(4),
    );
  });

  it("surfaces a Checkout success return and refreshes billing state", async () => {
    const fetchImpl = billingFetch();
    const services = billingServices(fetchImpl);

    render(
      <ServicesProvider services={services}>
        <MemoryRouter initialEntries={["/billing?checkout=success"]}>
          <BillingPage />
        </MemoryRouter>
      </ServicesProvider>,
    );

    expect(await screen.findByText("Checkout returned successfully")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/webhook arrives/i);
    await waitFor(() => expect((fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls).toHaveLength(2));

    await userEvent.click(screen.getByRole("button", { name: /refresh billing status/i }));

    await waitFor(() => expect((fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls).toHaveLength(4));
  });

  it("surfaces a Checkout cancellation return without implying payment changes", async () => {
    const fetchImpl = billingFetch();
    const services = billingServices(fetchImpl);

    render(
      <ServicesProvider services={services}>
        <MemoryRouter initialEntries={["/billing?checkout=cancel"]}>
          <BillingPage />
        </MemoryRouter>
      </ServicesProvider>,
    );

    expect(await screen.findByText("Checkout was canceled")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/No subscription changes were applied/i);
  });
});
