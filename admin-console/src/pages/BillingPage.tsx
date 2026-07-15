import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
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
import { useApiClient, useServices } from "../services";

const ACTIVE_STATUSES = new Set(["active", "trialing"]);
const MANAGED_SUBSCRIPTION_STATUSES = new Set([
  "active",
  "trialing",
  "checkout_pending",
  "checkout_completed",
  "incomplete",
  "past_due",
  "unpaid",
]);

type BillingPageProps = {
  redirectToHostedUrl?: (url: string) => void;
};

export function BillingPage({ redirectToHostedUrl = (url) => window.location.assign(url) }: BillingPageProps = {}) {
  const { auth } = useServices();
  const client = useApiClient();
  const [searchParams] = useSearchParams();
  const [status, setStatus] = useState<BillingStatus | null>(null);
  const [events, setEvents] = useState<BillingEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"checkout" | "portal" | "refresh" | null>(null);
  const checkoutResult = checkoutReturn(searchParams.get("checkout"));

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
      const url = await createBillingCheckoutSession(client, checkoutRedirectUrls());
      redirectToHostedUrl(url);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(null);
    }
  }

  async function openPortal() {
    setBusy("portal");
    try {
      const url = await createBillingPortalSession(client, {
        returnUrl: billingRouteUrl(),
      });
      redirectToHostedUrl(url);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(null);
    }
  }

  async function refreshBilling() {
    setBusy("refresh");
    try {
      await load();
    } finally {
      setBusy(null);
    }
  }

  const state = status?.subscriptionStatus ?? "loading";
  const hasLiveSubscription = ACTIVE_STATUSES.has(state);
  const subscriptionStateLabel = status
    ? hasLiveSubscription
      ? `subscription ${state}`
      : formatSubscriptionState(state)
    : "loading";
  const subscriptionStateDetail = status
    ? hasLiveSubscription
      ? status.entitlementReason
      : subscriptionStateDetailFor(state)
    : "Loading subscription state";
  const hasManagedSubscription = Boolean(
    status && (MANAGED_SUBSCRIPTION_STATUSES.has(state) || status.stripeSubscriptionId),
  );
  const canManagePayments = auth.hasScope("write");
  const needsCheckoutSetup = Boolean(status && (!status.checkoutReady || !status.liveModeReady));
  const canStartCheckout = Boolean(
    canManagePayments
      && status?.checkoutReady
      && status.liveModeReady
      && !hasManagedSubscription
      && busy === null,
  );
  const canOpenPortal = Boolean(
    canManagePayments
      && status?.portalReady
      && status.stripeCustomerId
      && status.liveModeReady
      && busy === null,
  );
  const checkoutButtonLabel = !canManagePayments
    ? "Read-only access"
    : hasLiveSubscription
    ? "Subscription active"
    : state === "checkout_pending"
      ? "Checkout pending"
    : hasManagedSubscription
      ? "Use Customer Portal"
    : status && !status.checkoutReady
      ? "Configure Checkout"
    : status && !status.liveModeReady
      ? "Complete setup"
    : busy === "checkout"
      ? "Opening Checkout..."
      : "Start subscription";

  return (
    <div>
      <PageHeader
        title="Billing"
        actions={
          <button onClick={refreshBilling} disabled={busy !== null || !status}>
            {busy === "refresh" ? "Refreshing..." : "Refresh billing status"}
          </button>
        }
      />
      {error && <p role="alert" className="alert">{error}</p>}
      {checkoutResult && (
        <section
          role="status"
          className={`alert billing-return ${checkoutResult.kind === "success" ? "info" : "warn"}`}
        >
          <div>
            <strong>{checkoutResult.title}</strong>
            <p>{checkoutResult.detail}</p>
          </div>
        </section>
      )}

      <section className="billing-hero">
        <div>
          <p className="eyebrow">Tenant subscription</p>
          <h2>{status?.tenantName ?? "Loading tenant billing"}</h2>
          <p className="muted">
            Stripe hosts payment collection and subscription management; doc-ray stores only tenant
            billing identifiers and webhook-derived subscription state.
          </p>
        </div>
        <div className={`billing-state ${hasLiveSubscription ? "good" : "attention"}`}>
          <span>{subscriptionStateLabel}</span>
          <small>{subscriptionStateDetail}</small>
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
            <ReadinessItem
              ready={Boolean(status && status.configurationIssues.length === 0)}
              label="Live-safe configuration"
            />
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
          {status && status.configurationIssues.length > 0 && (
            <p className="alert warn">
              Fix: {status.configurationIssues.join(", ")}
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
          <button onClick={openCheckout} disabled={!canStartCheckout}>
            {checkoutButtonLabel}
          </button>
          {hasManagedSubscription && (
            <p className="muted compact-note">
              Use the Customer Portal to change payment method, complete recovery, or manage the
              existing subscription.
            </p>
          )}
          {needsCheckoutSetup && !hasManagedSubscription && (
            <p className="muted compact-note">
              Complete live readiness before opening Stripe Checkout.
            </p>
          )}
          {!canManagePayments && (
            <p className="muted compact-note">
              Starting or changing a Stripe subscription requires write access.
            </p>
          )}
        </section>

        <section className="card billing-panel">
          <h2>Payment management</h2>
          <dl className="detail-list">
            <div><dt>Customer</dt><dd>{status?.stripeCustomerId ?? "not created"}</dd></div>
            <div><dt>Billing email</dt><dd>{status?.billingEmail ?? "not collected"}</dd></div>
            <div><dt>Subscription</dt><dd>{status?.stripeSubscriptionId ?? "none"}</dd></div>
            <div><dt>Latest invoice</dt><dd>{status?.latestInvoiceId ?? "none"}</dd></div>
          </dl>
          <button onClick={openPortal} disabled={!canOpenPortal}>
            {busy === "portal" ? "Opening Portal..." : "Manage payment method"}
          </button>
          {status?.stripeCustomerId && !status.liveModeReady && (
            <p className="muted compact-note">
              Complete live readiness before opening the Customer Portal.
            </p>
          )}
          {!canManagePayments && (
            <p className="muted compact-note">
              Managing payment methods requires write access.
            </p>
          )}
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
                  <th>Amount</th>
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
                    <td>{formatStripeAmount(event)}</td>
                    <td>{event.objectId ?? event.customerId ?? "n/a"}</td>
                    <td>{event.subscriptionId ?? "n/a"}</td>
                    <td>{formatDateTime(event.processedAt)}</td>
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

function formatSubscriptionState(state: string): string {
  if (state === "none") {
    return "no subscription";
  }
  return state.replace(/_/g, " ");
}

function subscriptionStateDetailFor(state: string): string {
  if (state === "none") {
    return "No Stripe subscription has been recorded";
  }
  return `Stripe subscription is ${formatSubscriptionState(state)}`;
}

function checkoutRedirectUrls() {
  return {
    successUrl: billingRouteUrl("checkout=success"),
    cancelUrl: billingRouteUrl("checkout=cancel"),
  };
}

function billingRouteUrl(search?: string) {
  const url = new URL("/billing", window.location.origin);
  if (search) {
    url.search = search;
  }
  return url.toString();
}

function checkoutReturn(value: string | null): { kind: "success" | "cancel"; title: string; detail: string } | null {
  if (value === "success") {
    return {
      kind: "success",
      title: "Checkout returned successfully",
      detail: "Stripe accepted the Checkout return. Refresh after the webhook arrives to confirm the tenant subscription state.",
    };
  }
  if (value === "cancel") {
    return {
      kind: "cancel",
      title: "Checkout was canceled",
      detail: "No subscription changes were applied. Start a new Checkout session when the tenant is ready.",
    };
  }
  return null;
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

function formatStripeAmount(event: BillingEvent): string {
  const amount = stripeDisplayAmount(event);
  if (amount === null || amount === undefined || !event.currency) return "n/a";
  const currency = event.currency.toUpperCase();
  const formatter = new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
  });
  const digits = formatter.resolvedOptions().maximumFractionDigits ?? 2;
  return formatter.format(amount / 10 ** digits);
}

function stripeDisplayAmount(event: BillingEvent): number | null {
  if (
    event.eventType === "invoice.payment_failed"
    || event.eventType === "invoice.finalization_failed"
    || event.objectStatus === "open"
  ) {
    return event.amountDue ?? event.amountTotal ?? event.amountPaid;
  }
  return event.amountPaid ?? event.amountTotal ?? event.amountDue;
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}
