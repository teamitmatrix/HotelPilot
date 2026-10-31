import { fetchApi } from "./api";

export interface BillingConfig {
  configured: boolean;
  publishable_key: string;
}

export interface StripeSubscriptionInfo {
  tenant_id?: string;
  stripe_customer_id?: string;
  stripe_subscription_id?: string | null;
  stripe_price_id?: string | null;
  plan_code?: string | null;
  status?: string | null;
  current_period_end?: string | null;
  cancel_at_period_end?: boolean;
}

export async function getBillingConfig(): Promise<BillingConfig> {
  const data = await fetchApi("/billing/config");
  const platform = (data.platform_stripe as Record<string, unknown>) || {};
  return {
    configured: Boolean(platform.configured),
    publishable_key: String(platform.publishable_key || ""),
  };
}

export async function getSubscriptionStatus(tenantId?: string) {
  const qs = tenantId ? `?tenant_id=${encodeURIComponent(tenantId)}` : "";
  const data = await fetchApi(`/billing/subscription${qs}`);
  return {
    platformStripeConfigured: Boolean(data.platform_stripe_configured),
    hotelStripeConfigured: Boolean(data.hotel_stripe_configured),
    hotelPublishableKey: String(data.hotel_publishable_key || ""),
    tenant: (data.tenant as Record<string, unknown>) || {},
    subscription: (data.subscription as StripeSubscriptionInfo | null) || null,
  };
}

export async function createCheckoutSession(body: {
  plan_code: string;
  billing_cycle?: "monthly" | "annual";
  success_url?: string;
  cancel_url?: string;
  trial_days?: number;
  tenant_id?: string;
}): Promise<{ checkout_url: string; session_id: string }> {
  const data = await fetchApi("/billing/checkout-session", {
    method: "POST",
    body: JSON.stringify(body),
  });
  if (data.status !== "ok" || !data.checkout_url) {
    throw new Error((data.message as string) || "Could not start Stripe Checkout");
  }
  return {
    checkout_url: String(data.checkout_url),
    session_id: String(data.session_id || ""),
  };
}

export async function createPortalSession(body?: {
  return_url?: string;
  tenant_id?: string;
}): Promise<{ portal_url: string }> {
  const data = await fetchApi("/billing/portal-session", {
    method: "POST",
    body: JSON.stringify(body || {}),
  });
  if (data.status !== "ok" || !data.portal_url) {
    throw new Error((data.message as string) || "Could not open billing portal");
  }
  return { portal_url: String(data.portal_url) };
}

export async function getHotelPaymentsStatus() {
  const data = await fetchApi("/billing/hotel-payments");
  return {
    configured: Boolean(data.configured),
    gateway: String(data.gateway || "none"),
    publishableKey: String(data.publishable_key || ""),
  };
}

export async function createFolioPaymentIntent(body: {
  folio_id: string;
  amount?: string;
  currency?: string;
  description?: string;
}): Promise<{
  client_secret: string;
  payment_intent_id: string;
  publishable_key: string;
  amount: string;
  currency: string;
}> {
  const data = await fetchApi("/billing/folio/payment-intent", {
    method: "POST",
    body: JSON.stringify(body),
  });
  if (data.status !== "ok" || !data.client_secret) {
    throw new Error((data.message as string) || "Could not create card payment");
  }
  return {
    client_secret: String(data.client_secret),
    payment_intent_id: String(data.payment_intent_id),
    publishable_key: String(data.publishable_key),
    amount: String(data.amount || ""),
    currency: String(data.currency || "usd"),
  };
}

export async function confirmFolioPayment(body: {
  folio_id: string;
  payment_intent_id: string;
}): Promise<{
  settled: boolean;
  amount?: string;
  gl_posted?: boolean;
  already_posted?: boolean;
  message?: string;
}> {
  const data = await fetchApi("/billing/folio/confirm", {
    method: "POST",
    body: JSON.stringify(body),
  });
  if (data.status !== "ok") {
    throw new Error((data.message as string) || "Could not post folio payment");
  }
  return {
    settled: Boolean(data.settled),
    amount: data.amount != null ? String(data.amount) : undefined,
    gl_posted: Boolean(data.gl_posted),
    already_posted: Boolean(data.already_posted),
    message: data.message != null ? String(data.message) : undefined,
  };
}

export async function quoteFolioDeposit(folioId: string): Promise<{
  amount: string;
  currency: string;
  reservation_id?: string;
}> {
  const data = await fetchApi(
    `/billing/folio/deposit-quote?folio_id=${encodeURIComponent(folioId)}`,
  );
  if (data.status !== "ok") {
    throw new Error((data.message as string) || "Could not quote deposit");
  }
  return {
    amount: String(data.amount || "0"),
    currency: String(data.currency || "USD"),
    reservation_id:
      data.reservation_id != null ? String(data.reservation_id) : undefined,
  };
}

export async function createFolioDepositIntent(body: {
  folio_id: string;
  amount?: string;
  currency?: string;
  description?: string;
}): Promise<{
  client_secret: string;
  payment_intent_id: string;
  publishable_key: string;
  amount: string;
  currency: string;
}> {
  const data = await fetchApi("/billing/folio/deposit-intent", {
    method: "POST",
    body: JSON.stringify(body),
  });
  if (data.status !== "ok" || !data.client_secret) {
    throw new Error((data.message as string) || "Could not create deposit payment");
  }
  return {
    client_secret: String(data.client_secret),
    payment_intent_id: String(data.payment_intent_id),
    publishable_key: String(data.publishable_key),
    amount: String(data.amount || ""),
    currency: String(data.currency || "usd"),
  };
}

export async function confirmFolioDeposit(body: {
  folio_id: string;
  payment_intent_id: string;
}): Promise<{
  posted: boolean;
  amount?: string;
  gl_posted?: boolean;
  already_posted?: boolean;
  message?: string;
}> {
  const data = await fetchApi("/billing/folio/deposit-confirm", {
    method: "POST",
    body: JSON.stringify(body),
  });
  if (data.status !== "ok") {
    throw new Error((data.message as string) || "Could not post deposit");
  }
  return {
    posted: Boolean(data.posted),
    amount: data.amount != null ? String(data.amount) : undefined,
    gl_posted: Boolean(data.gl_posted),
    already_posted: Boolean(data.already_posted),
    message: data.message != null ? String(data.message) : undefined,
  };
}
