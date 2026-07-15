import { ApiClient } from "./client";

export interface BillingStatus {
  tenantId: string;
  tenantSlug: string;
  tenantName: string;
  stripeCustomerId: string | null;
  billingEmail: string | null;
  stripeSubscriptionId: string | null;
  stripePriceId: string | null;
  subscriptionStatus: string;
  currentPeriodEnd: string | null;
  cancelAtPeriodEnd: boolean;
  latestInvoiceId: string | null;
  checkoutSessionId: string | null;
  subscriptionUpdatedAt: string | null;
  checkoutReady: boolean;
  portalReady: boolean;
  webhookReady: boolean;
  webhookEndpointUrl: string | null;
  liveModeReady: boolean;
  missingConfiguration: string[];
  configurationIssues: string[];
  requiredWebhookEvents: string[];
  enforcementEnabled: boolean;
  entitled: boolean;
  entitlementReason: string;
}

export interface BillingEvent {
  stripeEventId: string;
  eventType: string;
  livemode: boolean;
  processedAt: string | null;
  createdAt: string;
  objectId: string | null;
  customerId: string | null;
  subscriptionId: string | null;
  objectStatus: string | null;
  latestInvoiceId: string | null;
  amountPaid: number | null;
  amountDue: number | null;
  amountTotal: number | null;
  currency: string | null;
  hostedInvoiceUrl: string | null;
  invoicePdf: string | null;
}

export async function fetchBillingStatus(client: ApiClient): Promise<BillingStatus> {
  const data = await client.graphql<{ billingStatus: BillingStatus }>(`
    query BillingStatus {
      billingStatus {
        tenantId
        tenantSlug
        tenantName
        stripeCustomerId
        billingEmail
        stripeSubscriptionId
        stripePriceId
        subscriptionStatus
        currentPeriodEnd
        cancelAtPeriodEnd
        latestInvoiceId
        checkoutSessionId
        subscriptionUpdatedAt
        checkoutReady
        portalReady
        webhookReady
        webhookEndpointUrl
        liveModeReady
        missingConfiguration
        configurationIssues
        requiredWebhookEvents
        enforcementEnabled
        entitled
        entitlementReason
      }
    }
  `);
  return data.billingStatus;
}

export async function fetchBillingEvents(client: ApiClient, limit = 25): Promise<BillingEvent[]> {
  const data = await client.graphql<{ billingEvents: BillingEvent[] }>(
    `
      query BillingEvents($limit: Int!) {
        billingEvents(limit: $limit) {
          stripeEventId
          eventType
          livemode
          processedAt
          createdAt
          objectId
          customerId
          subscriptionId
          objectStatus
          latestInvoiceId
          amountPaid
          amountDue
          amountTotal
          currency
          hostedInvoiceUrl
          invoicePdf
        }
      }
    `,
    { limit },
  );
  return data.billingEvents;
}

export async function createBillingCheckoutSession(
  client: ApiClient,
  input: { successUrl?: string; cancelUrl?: string } = {},
): Promise<string> {
  const data = await client.graphql<{ createBillingCheckoutSession: { url: string } }>(
    `
      mutation CreateBillingCheckoutSession($successUrl: String, $cancelUrl: String) {
        createBillingCheckoutSession(successUrl: $successUrl, cancelUrl: $cancelUrl) {
          url
        }
      }
    `,
    { successUrl: input.successUrl ?? null, cancelUrl: input.cancelUrl ?? null },
  );
  return data.createBillingCheckoutSession.url;
}

export async function createBillingPortalSession(
  client: ApiClient,
  input: { returnUrl?: string } = {},
): Promise<string> {
  const data = await client.graphql<{ createBillingPortalSession: { url: string } }>(
    `
      mutation CreateBillingPortalSession($returnUrl: String) {
        createBillingPortalSession(returnUrl: $returnUrl) {
          url
        }
      }
    `,
    { returnUrl: input.returnUrl ?? null },
  );
  return data.createBillingPortalSession.url;
}
