import { useEffect, useState } from "react";
import {
  createBillingCheckoutSession,
  createBillingPortalSession,
  fetchBillingEvents,
  fetchBillingStatus,
  type BillingEvent,
  type BillingStatus,
} from "../api/billing";
import { ApiError } from "../api/client";
import { PageHeader } from "../components/common";
import { useApiClient } from "../services";

const ACTIVE_STATUSES = new Set(["active", "trialing"]);

export function BillingPage() {
  const client = useApiClient();
  const [status, setStatus] = useState<BillingStatus | null>(null);
  const [events, setEvents] = useState<BillingEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"checkout" | "portal" | null>(null);

  async function load() {
    try {
      setError(null);
      const [billingStatus, billingEvents] = await Promise.all([
        fetchBillingStatus(client),
        fetchBillingEvents(client, 25),
      ]);
      setStatus(billingStatus);
      setEvents(billingEvents);
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
        <section className="card billing-panel billing-readiness">
          <div className="section-title-row">
            <h2>Live readiness</h2>
            <span className={`status-pill ${status?.liveModeReady ? "good" : "attention"}`}>
              {status?.liveModeReady ? "ready" : "needs setup"}
            </span>
          </div>
          <ul className="readiness-list">
            <ReadinessItem ready={Boolean(status?.checkoutReady)} label="Checkout sessions" />
            <ReadinessItem ready={Boolean(status?.portalReady)} label="Customer portal" />
            <ReadinessItem ready={Boolean(status?.webhookReady)} label="Signed Stripe webhook" />
            <ReadinessItem ready={Boolean(status?.enforcementEnabled)} label="Paid-action enforcement" />
          </ul>
          <dl className="detail-list compact">
            <div><dt>Webhook URL</dt><dd>{status?.webhookEndpointUrl ?? "set DOCRAY_PUBLIC_BASE_URL"}</dd></div>
            <div><dt>Events</dt><dd>{status?.requiredWebhookEvents.join(", ") || "loading"}</dd></div>
          </dl>
          {status && status.missingConfiguration.length > 0 && (
            <p className="alert warn">
              Missing: {status.missingConfiguration.join(", ")}
            </p>
          )}
        </section>

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

      <section className="card billing-panel billing-activity">
        <div className="section-title-row">
          <h2>Payment activity</h2>
          <span className="muted">{events.length} recent events</span>
        </div>
        {events.length > 0 ? (
          <div className="table-wrap">
            <table className="grid billing-events">
              <thead>
                <tr>
                  <th>Event</th>
                  <th>Status</th>
                  <th>Object</th>
                  <th>Subscription</th>
                  <th>Processed</th>
                </tr>
              </thead>
              <tbody>
                {events.map((event) => (
                  <tr key={event.stripeEventId}>
                    <td>
                      <strong>{event.eventType}</strong>
                      <span>{event.livemode ? "live" : "test"} · {event.stripeEventId}</span>
                    </td>
                    <td>{event.objectStatus ?? "received"}</td>
                    <td>{event.objectId ?? event.customerId ?? "n/a"}</td>
                    <td>{event.subscriptionId ?? "n/a"}</td>
                    <td>{formatDateTime(event.processedAt ?? event.createdAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="muted">
            No Stripe webhook events have been recorded for this tenant yet.
          </p>
        )}
      </section>

      {status && !status.liveModeReady && (
        <p role="note" className="alert warn">
          Stripe billing is partially configured. Complete the live-readiness checklist before
          taking live payments.
        </p>
      )}
    </div>
  );
}

function ReadinessItem({ ready, label }: { ready: boolean; label: string }) {
  return (
    <li className={ready ? "ready" : "missing"}>
      <span aria-hidden="true">{ready ? "OK" : "!"}</span>
      {label}
    </li>
  );
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "not scheduled";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(value));
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return "not processed";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}
