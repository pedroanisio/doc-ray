import { useEffect, useState } from "react";
import {
  createBillingCheckoutSession,
  createBillingPortalSession,
  fetchBillingStatus,
  type BillingStatus,
} from "../api/billing";
import { ApiError } from "../api/client";
import { PageHeader } from "../components/common";
import { useApiClient } from "../services";

const ACTIVE_STATUSES = new Set(["active", "trialing"]);

export function BillingPage() {
  const client = useApiClient();
  const [status, setStatus] = useState<BillingStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"checkout" | "portal" | null>(null);

  async function load() {
    try {
      setError(null);
      setStatus(await fetchBillingStatus(client));
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function openCheckout() {
    setBusy("checkout");
    try {
      const url = await createBillingCheckoutSession(client);
      window.location.assign(url);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(null);
    }
  }

  async function openPortal() {
    setBusy("portal");
    try {
      const url = await createBillingPortalSession(client);
      window.location.assign(url);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(null);
    }
  }

  const state = status?.subscriptionStatus ?? "loading";
  const isActive = Boolean(status?.entitled ?? ACTIVE_STATUSES.has(state));

  return (
    <div>
      <PageHeader title="Billing" />
      {error && <p role="alert" className="alert">{error}</p>}

      <section className="billing-hero">
        <div>
          <p className="eyebrow">Tenant subscription</p>
          <h2>{status?.tenantName ?? "Loading tenant billing"}</h2>
          <p className="muted">
            Stripe hosts payment collection and subscription management; doc-ray stores only tenant
            billing identifiers and webhook-derived subscription state.
          </p>
        </div>
        <div className={`billing-state ${isActive ? "good" : "attention"}`}>
          <span>{status?.entitled ? "entitled" : state}</span>
          <small>{status?.entitlementReason ?? "Loading entitlement policy"}</small>
        </div>
      </section>

      <div className="billing-grid">
        <section className="card billing-panel">
          <h2>Entitlement</h2>
          <dl className="detail-list">
            <div><dt>Enforcement</dt><dd>{status?.enforcementEnabled ? "enabled" : "disabled"}</dd></div>
            <div><dt>Paid access</dt><dd>{status?.entitled ? "allowed" : "blocked"}</dd></div>
            <div><dt>Reason</dt><dd>{status?.entitlementReason ?? "loading"}</dd></div>
          </dl>
          {!status?.entitled && (
            <p className="alert warn">
              Paid SaaS actions are blocked until Stripe reports an active or trialing
              subscription for this tenant.
            </p>
          )}
        </section>

        <section className="card billing-panel">
          <h2>Plan</h2>
          <dl className="detail-list">
            <div><dt>Price</dt><dd>{status?.stripePriceId ?? "not selected"}</dd></div>
            <div><dt>Renews</dt><dd>{formatDate(status?.currentPeriodEnd)}</dd></div>
            <div><dt>Cancel at period end</dt><dd>{status?.cancelAtPeriodEnd ? "yes" : "no"}</dd></div>
          </dl>
          <button onClick={openCheckout} disabled={!status?.checkoutReady || busy !== null}>
            {busy === "checkout" ? "Opening Checkout..." : "Start subscription"}
          </button>
        </section>

        <section className="card billing-panel">
          <h2>Payment management</h2>
          <dl className="detail-list">
            <div><dt>Customer</dt><dd>{status?.stripeCustomerId ?? "not created"}</dd></div>
            <div><dt>Subscription</dt><dd>{status?.stripeSubscriptionId ?? "none"}</dd></div>
            <div><dt>Latest invoice</dt><dd>{status?.latestInvoiceId ?? "none"}</dd></div>
          </dl>
          <button onClick={openPortal} disabled={!status?.portalReady || !status?.stripeCustomerId || busy !== null}>
            {busy === "portal" ? "Opening Portal..." : "Manage payment method"}
          </button>
        </section>
      </div>

      {status && (!status.checkoutReady || !status.portalReady) && (
        <p role="note" className="alert warn">
          Stripe billing is partially configured. Set the Stripe secret, recurring price, webhook
          secret, and redirect URLs before taking live payments.
        </p>
      )}
    </div>
  );
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "not scheduled";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(value));
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}
