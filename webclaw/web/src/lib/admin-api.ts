/**
 * Admin API hooks for RBAC management (roles, permissions, users).
 * All endpoints require System Manager role.
 */
import { fetchApi } from "./api";
import { getAccessToken } from "./auth";

// ── Types ────────────────────────────────────────────────────────────────────

export interface Role {
  id: string;
  name: string;
  description: string;
  is_system: boolean;
  created_at: string;
  permission_count?: number;
  user_count?: number;
}

export interface Permission {
  id: string;
  skill: string;
  action_pattern: string;
  allowed: boolean;
}

export interface AdminUser {
  id: string;
  username: string;
  email: string;
  full_name: string;
  status: string;
  last_login: string | null;
  created_at: string;
  roles: { id: string; name: string }[];
  tenant_id?: string | null;
  tenants?: {
    tenant_id?: string | null;
    hotel_name?: string | null;
    role?: string | null;
    role_name?: string | null;
  }[];
  active_sessions?: number;
}

// ── Roles ────────────────────────────────────────────────────────────────────

export async function listRoles(): Promise<Role[]> {
  const data = await fetchApi("/admin/roles");
  return (data.roles as Role[]) || [];
}

export async function createRole(name: string, description: string): Promise<Role> {
  const data = await fetchApi("/admin/roles", {
    method: "POST",
    body: JSON.stringify({ name, description }),
  });
  return data.role as Role;
}

export async function updateRole(roleId: string, name: string, description: string): Promise<void> {
  await fetchApi(`/admin/roles/${roleId}`, {
    method: "PUT",
    body: JSON.stringify({ name, description }),
  });
}

export async function deleteRole(roleId: string): Promise<void> {
  await fetchApi(`/admin/roles/${roleId}`, { method: "DELETE" });
}

// ── Permissions ──────────────────────────────────────────────────────────────

export async function listPermissions(roleId: string): Promise<Permission[]> {
  const data = await fetchApi(`/admin/roles/${roleId}/permissions`);
  return (data.permissions as Permission[]) || [];
}

export async function addPermission(
  roleId: string,
  skill: string,
  actionPattern: string,
  allowed: boolean = true,
): Promise<Permission> {
  const data = await fetchApi(`/admin/roles/${roleId}/permissions`, {
    method: "POST",
    body: JSON.stringify({ skill, action_pattern: actionPattern, allowed }),
  });
  return data.permission as Permission;
}

export async function removePermission(roleId: string, permId: string): Promise<void> {
  await fetchApi(`/admin/roles/${roleId}/permissions/${permId}`, { method: "DELETE" });
}

// ── Users ────────────────────────────────────────────────────────────────────

export async function listUsers(params?: {
  q?: string;
  tenant_id?: string;
  status?: string;
}): Promise<AdminUser[]> {
  const sp = new URLSearchParams();
  if (params?.q) sp.set("q", params.q);
  if (params?.tenant_id) sp.set("tenant_id", params.tenant_id);
  if (params?.status) sp.set("status", params.status);
  const qs = sp.toString();
  const data = await fetchApi(`/admin/users${qs ? `?${qs}` : ""}`);
  return (data.users as AdminUser[]) || [];
}

export async function forceLogoutUser(userId: string): Promise<number> {
  const data = await fetchApi(`/admin/users/${userId}/force-logout`, { method: "POST" });
  return Number(data.sessions_revoked ?? 0);
}

export async function resetUserPassword(
  userId: string,
  password?: string,
): Promise<{ temporary_password?: string | null; generated: boolean; sessions_revoked: number }> {
  const data = await fetchApi(`/admin/users/${userId}/reset-password`, {
    method: "POST",
    body: JSON.stringify(password ? { password } : {}),
  });
  return {
    temporary_password: (data.temporary_password as string) || null,
    generated: Boolean(data.generated),
    sessions_revoked: Number(data.sessions_revoked ?? 0),
  };
}

export async function createUser(
  email: string,
  password: string,
  fullName: string,
  roleIds: string[],
): Promise<AdminUser> {
  const data = await fetchApi("/admin/users", {
    method: "POST",
    body: JSON.stringify({ email, password, full_name: fullName, role_ids: roleIds }),
  });
  return data.user as AdminUser;
}

export async function updateUser(
  userId: string,
  updates: { full_name?: string; status?: string; password?: string },
): Promise<void> {
  await fetchApi(`/admin/users/${userId}`, {
    method: "PUT",
    body: JSON.stringify(updates),
  });
}

export async function assignRole(userId: string, roleId: string): Promise<void> {
  await fetchApi(`/admin/users/${userId}/roles`, {
    method: "POST",
    body: JSON.stringify({ role_id: roleId }),
  });
}

export async function removeUserRole(userId: string, roleId: string): Promise<void> {
  await fetchApi(`/admin/users/${userId}/roles/${roleId}`, { method: "DELETE" });
}

// ── Hotel tenants ────────────────────────────────────────────────────────────

export interface HotelTenant {
  id: string;
  hotel_name: string;
  admin_email: string;
  timezone: string;
  country: string;
  currency: string;
  subscription_tier: string;
  plan_code: string;
  plan_name: string;
  max_rooms: number | null;
  max_staff?: number | null;
  status: string;
  lifecycle?: string;
  created_at: string;
  company_name?: string;
  room_count?: number;
  trial_ends_at?: string | null;
  discount_percent?: number;
  discount_note?: string | null;
  cancelled_at?: string | null;
  purge_after?: string | null;
}

export interface FeatureToggle {
  key: string;
  label: string;
  description?: string;
}

export interface SubscriptionPlan {
  id: string;
  code: string;
  name: string;
  billing_cycle: "monthly" | "annual";
  monthly_price: string;
  annual_price: string;
  max_rooms: number;
  unlimited_rooms: boolean;
  max_staff: number;
  unlimited_staff: boolean;
  features: Record<string, boolean>;
  is_active: boolean;
  sort_order: number;
  created_at?: string;
  updated_at?: string;
}

export interface SubscriptionEvent {
  id: string;
  tenant_id: string;
  event_type: string;
  detail: string;
  actor_email?: string | null;
  created_at: string;
}

export interface BillingOverride {
  trial_ends_at?: string | null;
  discount_percent: number;
  discount_note?: string | null;
}

export interface PlatformActivityEvent {
  id: string;
  kind: "hotel" | "billing" | "security" | "system";
  label: string;
  detail: string;
  created_at: string;
}

export interface PlatformHealth {
  database: "ok" | "down" | "degraded";
  api_latency_ms: number;
  workers: "ok" | "degraded";
  stripe?: "ok" | "unconfigured" | string;
  past_due_hotels?: number;
  status: "operational" | "degraded";
  label: string;
}

export interface PlatformAnalytics {
  active_hotels: number;
  total_hotels: number;
  suspended_hotels: number;
  cancelled_hotels: number;
  onboarding_hotels: number;
  active_subscriptions: number;
  revenue: number;
  subscription_mrr: number;
  subscription_arr?: number;
  new_signups_30d?: number;
  churn_30d?: number;
  failed_payments_open?: number;
  trials_ending_7d?: number;
  past_due_hotels?: number;
  booking_count: number;
  active_bookings: number;
  bookings_by_status: Record<string, number>;
  total_rooms: number;
  active_admin_users: number;
  activity: PlatformActivityEvent[];
  health: PlatformHealth;
}

export async function listTenants(): Promise<HotelTenant[]> {
  const data = await fetchApi("/admin/tenants");
  return (data.tenants as HotelTenant[]) || [];
}

export async function createTenant(payload: {
  hotel_name: string;
  admin_email: string;
  password: string;
  currency?: string;
  country?: string;
  timezone?: string;
  plan_code?: string;
}): Promise<HotelTenant> {
  const data = await fetchApi("/admin/tenants", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return data.tenant as HotelTenant;
}

export async function updateTenant(
  tenantId: string,
  updates: {
    plan_code?: string;
    hotel_name?: string;
    timezone?: string;
    country?: string;
    currency?: string;
  },
): Promise<HotelTenant> {
  const data = await fetchApi(`/admin/tenants/${tenantId}`, {
    method: "PATCH",
    body: JSON.stringify(updates),
  });
  return data.tenant as HotelTenant;
}

export async function pauseTenant(tenantId: string): Promise<void> {
  await fetchApi(`/admin/tenants/${tenantId}/pause`, { method: "POST" });
}

export async function resumeTenant(tenantId: string): Promise<void> {
  await fetchApi(`/admin/tenants/${tenantId}/resume`, { method: "POST" });
}

export async function deleteTenant(tenantId: string): Promise<{
  purge_after?: string;
  message?: string;
}> {
  const data = await fetchApi(`/admin/tenants/${tenantId}`, { method: "DELETE" });
  return {
    purge_after: data.purge_after as string | undefined,
    message: data.message as string | undefined,
  };
}

export async function purgeTenant(tenantId: string): Promise<void> {
  await fetchApi(`/admin/tenants/${tenantId}/purge`, { method: "POST" });
}

export async function impersonateTenant(
  tenantId: string,
  reason: string,
): Promise<{
  access_token: string;
  user: {
    user_id: string;
    email: string;
    full_name: string;
    tenant_id: string;
    is_superadmin: boolean;
    is_impersonating: boolean;
    impersonated_hotel?: string;
    impersonation_reason?: string;
    roles: { role_name: string }[];
  };
  hotel_name: string;
  reason: string;
  expires_in_minutes: number;
}> {
  const data = await fetchApi(`/admin/tenants/${tenantId}/impersonate`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
  return {
    access_token: String(data.access_token ?? ""),
    user: data.user as {
      user_id: string;
      email: string;
      full_name: string;
      tenant_id: string;
      is_superadmin: boolean;
      is_impersonating: boolean;
      impersonated_hotel?: string;
      impersonation_reason?: string;
      roles: { role_name: string }[];
    },
    hotel_name: String(data.hotel_name ?? ""),
    reason: String(data.reason ?? reason),
    expires_in_minutes: Number(data.expires_in_minutes ?? 15),
  };
}

export async function endImpersonationSession(): Promise<void> {
  await fetchApi("/auth/impersonate/end", { method: "POST" });
}

export interface TenantUsage {
  rooms: number;
  max_rooms: number | null;
  rooms_unlimited: boolean;
  rooms_over_limit: boolean;
  staff: number;
  max_staff: number | null;
  staff_unlimited: boolean;
  staff_over_limit: boolean;
}

export interface TenantStripeStatus {
  tenant_id?: string;
  stripe_customer_id?: string;
  stripe_subscription_id?: string | null;
  plan_code?: string | null;
  status?: string;
  current_period_end?: string | null;
  cancel_at_period_end?: boolean;
}

export interface OnboardingStep {
  key: string;
  title: string;
  description: string;
  sort_order: number;
  required: boolean;
  status: "pending" | "done" | "skipped" | "blocked" | string;
  completed_at?: string | null;
  completed_by?: string | null;
}

export interface OnboardingPipeline {
  tenant_id: string;
  hotel_name?: string | null;
  admin_email?: string | null;
  source?: string | null;
  status: string;
  owner_invite_status?: string | null;
  go_live_at?: string | null;
  go_live_by?: string | null;
  notes?: string;
  required_complete: boolean;
  can_go_live: boolean;
  steps: OnboardingStep[];
  progress: {
    done: number;
    total: number;
    required_done: number;
    required_total: number;
  };
}

export interface TenantDetailResponse {
  tenant: HotelTenant;
  usage: TenantUsage;
  billing_override: BillingOverride;
  stripe: TenantStripeStatus | null;
  subscription_events: SubscriptionEvent[];
  tickets: SupportTicket[];
  onboarding?: OnboardingPipeline | null;
}

export async function getTenantDetail(tenantId: string): Promise<TenantDetailResponse> {
  const data = await fetchApi(`/admin/tenants/${tenantId}`);
  return {
    tenant: data.tenant as HotelTenant,
    usage: data.usage as TenantUsage,
    billing_override: (data.billing_override as BillingOverride) || {
      trial_ends_at: null,
      discount_percent: 0,
      discount_note: null,
    },
    stripe: (data.stripe as TenantStripeStatus) || null,
    subscription_events: (data.subscription_events as SubscriptionEvent[]) || [],
    tickets: (data.tickets as SupportTicket[]) || [],
    onboarding: (data.onboarding as OnboardingPipeline) || null,
  };
}

export async function updateOnboardingStep(
  tenantId: string,
  stepKey: string,
  status: string,
): Promise<OnboardingPipeline> {
  const data = await fetchApi(`/admin/tenants/${tenantId}/onboarding/steps/${stepKey}`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
  return data.onboarding as OnboardingPipeline;
}

export async function goLiveTenant(
  tenantId: string,
  force = false,
): Promise<OnboardingPipeline> {
  const data = await fetchApi(`/admin/tenants/${tenantId}/onboarding/go-live`, {
    method: "POST",
    body: JSON.stringify({ force }),
  });
  return data.onboarding as OnboardingPipeline;
}

export async function listOnboardingPipelines(status?: string): Promise<{
  pipelines: (OnboardingPipeline & { progress?: OnboardingPipeline["progress"] })[];
  in_progress_count: number;
}> {
  const qs = status && status !== "all" ? `?status=${encodeURIComponent(status)}` : "";
  const data = await fetchApi(`/admin/onboarding${qs}`);
  return {
    pipelines: (data.pipelines as OnboardingPipeline[]) || [],
    in_progress_count: Number(data.in_progress_count ?? 0),
  };
}

export interface BillingOverviewItem {
  tenant_id: string;
  hotel_name: string;
  admin_email?: string | null;
  plan_code?: string | null;
  stripe_status: string;
  stripe_customer_id?: string | null;
  stripe_subscription_id?: string | null;
  current_period_end?: string | null;
  cancel_at_period_end?: boolean;
  updated_at?: string | null;
  hotel_status?: string | null;
}

export interface BillingFailedEvent {
  id: string;
  tenant_id: string;
  hotel_name: string;
  event_type: string;
  detail?: string | null;
  actor_email?: string | null;
  created_at: string;
}

export interface BillingTrialEnding {
  tenant_id: string;
  hotel_name: string;
  trial_ends_at: string;
  plan_code?: string | null;
  hotel_status?: string | null;
}

export interface BillingOverview {
  at_risk: BillingOverviewItem[];
  failed_events: BillingFailedEvent[];
  trials_ending: BillingTrialEnding[];
  counts: {
    at_risk: number;
    failed_events: number;
    trials_ending: number;
    plans: number;
  };
}

export async function getBillingOverview(): Promise<BillingOverview> {
  const data = await fetchApi("/admin/billing/overview");
  return {
    at_risk: (data.at_risk as BillingOverviewItem[]) || [],
    failed_events: (data.failed_events as BillingFailedEvent[]) || [],
    trials_ending: (data.trials_ending as BillingTrialEnding[]) || [],
    counts: {
      at_risk: Number(data.counts?.at_risk ?? 0),
      failed_events: Number(data.counts?.failed_events ?? 0),
      trials_ending: Number(data.counts?.trials_ending ?? 0),
      plans: Number(data.counts?.plans ?? 0),
    },
  };
}

export interface SupportTicketNote {
  id: string;
  ticket_id: string;
  body: string;
  author_email?: string | null;
  author_id?: string | null;
  created_at: string;
}

export interface SupportTicket {
  id: string;
  tenant_id: string;
  hotel_name?: string | null;
  subject: string;
  body: string;
  status: "open" | "in_progress" | "waiting" | "resolved" | "closed" | string;
  priority: "low" | "normal" | "high" | "urgent" | string;
  requester_email?: string | null;
  assignee_email?: string | null;
  created_by?: string | null;
  created_by_email?: string | null;
  created_at: string;
  updated_at: string;
  resolved_at?: string | null;
  sla_due_at?: string | null;
  first_response_at?: string | null;
  sla_breached?: boolean;
  notes?: SupportTicketNote[];
}

export interface SupportMacro {
  id: string;
  name: string;
  body: string;
  category: string;
  is_active: boolean;
  created_at?: string;
  updated_at?: string;
  updated_by?: string | null;
}

export async function listSupportTickets(params?: {
  status?: string;
  tenant_id?: string;
  q?: string;
  limit?: number;
  offset?: number;
}): Promise<{ tickets: SupportTicket[]; total: number }> {
  const sp = new URLSearchParams();
  if (params?.status) sp.set("status", params.status);
  if (params?.tenant_id) sp.set("tenant_id", params.tenant_id);
  if (params?.q) sp.set("q", params.q);
  if (params?.limit != null) sp.set("limit", String(params.limit));
  if (params?.offset != null) sp.set("offset", String(params.offset));
  const qs = sp.toString();
  const data = await fetchApi(`/admin/support-tickets${qs ? `?${qs}` : ""}`);
  return {
    tickets: (data.tickets as SupportTicket[]) || [],
    total: Number(data.total ?? 0),
  };
}

export async function createSupportTicket(payload: {
  tenant_id: string;
  subject: string;
  body?: string;
  priority?: string;
  requester_email?: string;
  hotel_name?: string;
}): Promise<SupportTicket> {
  const data = await fetchApi("/admin/support-tickets", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return data.ticket as SupportTicket;
}

export async function getSupportTicket(ticketId: string): Promise<SupportTicket> {
  const data = await fetchApi(`/admin/support-tickets/${ticketId}`);
  return data.ticket as SupportTicket;
}

export async function updateSupportTicket(
  ticketId: string,
  payload: {
    subject?: string;
    body?: string;
    status?: string;
    priority?: string;
    assignee_email?: string | null;
    note?: string;
  },
): Promise<SupportTicket> {
  const data = await fetchApi(`/admin/support-tickets/${ticketId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
  return data.ticket as SupportTicket;
}

export async function listSupportMacros(): Promise<SupportMacro[]> {
  const data = await fetchApi("/admin/support-macros");
  return (data.macros as SupportMacro[]) || [];
}

export async function createSupportMacro(payload: {
  name: string;
  body: string;
  category?: string;
}): Promise<SupportMacro> {
  const data = await fetchApi("/admin/support-macros", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return data.macro as SupportMacro;
}

export async function applySupportMacro(
  ticketId: string,
  macroId: string,
): Promise<SupportTicket> {
  const data = await fetchApi(`/admin/support-tickets/${ticketId}/apply-macro`, {
    method: "POST",
    body: JSON.stringify({ macro_id: macroId }),
  });
  return data.ticket as SupportTicket;
}

export async function listPlans(): Promise<{
  plans: SubscriptionPlan[];
  feature_catalog: FeatureToggle[];
}> {
  const data = await fetchApi("/admin/plans");
  const plans = ((data.plans as SubscriptionPlan[]) || []).map((p) => ({
    ...p,
    max_staff: p.max_staff ?? 5,
    unlimited_staff: (p.max_staff ?? 5) <= 0 || Boolean(p.unlimited_staff),
  }));
  return {
    plans,
    feature_catalog: (data.feature_catalog as FeatureToggle[]) || [],
  };
}

export async function createPlan(
  payload: Partial<SubscriptionPlan> & { code: string; name: string },
): Promise<SubscriptionPlan> {
  const data = await fetchApi("/admin/plans", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return data.plan as SubscriptionPlan;
}

export async function updatePlan(
  planId: string,
  payload: Partial<SubscriptionPlan>,
): Promise<SubscriptionPlan> {
  const data = await fetchApi(`/admin/plans/${planId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
  return data.plan as SubscriptionPlan;
}

export async function deletePlan(planId: string): Promise<void> {
  await fetchApi(`/admin/plans/${planId}`, { method: "DELETE" });
}

export async function getSubscriptionHistory(tenantId: string): Promise<{
  events: SubscriptionEvent[];
  override: BillingOverride;
  tenant_id: string;
}> {
  const data = await fetchApi(`/admin/tenants/${tenantId}/subscription-history`);
  return {
    events: (data.events as SubscriptionEvent[]) || [],
    override: (data.override as BillingOverride) || {
      trial_ends_at: null,
      discount_percent: 0,
      discount_note: null,
    },
    tenant_id: String(data.tenant_id ?? tenantId),
  };
}

export async function applyBillingOverride(
  tenantId: string,
  payload: {
    action: "extend_trial" | "apply_discount" | "clear_discount" | "clear_trial";
    days?: number;
    discount_percent?: number;
    note?: string;
  },
): Promise<{ override: BillingOverride; message: string }> {
  const data = await fetchApi(`/admin/tenants/${tenantId}/billing-override`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return {
    override: (data.override as BillingOverride) || {
      trial_ends_at: null,
      discount_percent: 0,
      discount_note: null,
    },
    message: String(data.message ?? "Updated"),
  };
}

export async function getPlatformCohorts(params?: {
  dimension?: string;
  date_from?: string;
  date_to?: string;
}): Promise<{ dimension: string; cohorts: AnalyticsCohort[] }> {
  const qs = new URLSearchParams();
  if (params?.dimension) qs.set("dimension", params.dimension);
  if (params?.date_from) qs.set("date_from", params.date_from);
  if (params?.date_to) qs.set("date_to", params.date_to);
  const data = await fetchApi(`/admin/analytics/cohorts${qs.toString() ? `?${qs}` : ""}`);
  return {
    dimension: String(data.dimension || params?.dimension || "plan"),
    cohorts: (data.cohorts as AnalyticsCohort[]) || [],
  };
}

export async function downloadPlatformCohortsCsv(params?: {
  dimension?: string;
  date_from?: string;
  date_to?: string;
}): Promise<void> {
  const token = getAccessToken();
  const qs = new URLSearchParams();
  if (params?.dimension) qs.set("dimension", params.dimension);
  if (params?.date_from) qs.set("date_from", params.date_from);
  if (params?.date_to) qs.set("date_to", params.date_to);
  const res = await fetch(
    `/api/v1/admin/analytics/export.csv${qs.toString() ? `?${qs}` : ""}`,
    {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      credentials: "include",
    },
  );
  if (!res.ok) throw new Error(`Export failed (${res.status})`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `platform-cohorts-${params?.dimension || "plan"}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

export async function getPlatformAnalytics(): Promise<PlatformAnalytics> {
  const data = await fetchApi("/admin/analytics");
  const raw = (data.analytics || {}) as Partial<PlatformAnalytics>;
  return {
    active_hotels: raw.active_hotels ?? 0,
    total_hotels: raw.total_hotels ?? 0,
    suspended_hotels: raw.suspended_hotels ?? 0,
    cancelled_hotels: raw.cancelled_hotels ?? 0,
    onboarding_hotels: raw.onboarding_hotels ?? 0,
    active_subscriptions: raw.active_subscriptions ?? raw.active_hotels ?? 0,
    revenue: raw.revenue ?? 0,
    subscription_mrr: raw.subscription_mrr ?? 0,
    subscription_arr: raw.subscription_arr ?? (raw.subscription_mrr ?? 0) * 12,
    new_signups_30d: raw.new_signups_30d ?? 0,
    churn_30d: raw.churn_30d ?? 0,
    failed_payments_open: raw.failed_payments_open ?? 0,
    trials_ending_7d: raw.trials_ending_7d ?? 0,
    past_due_hotels: raw.past_due_hotels ?? 0,
    booking_count: raw.booking_count ?? 0,
    active_bookings: raw.active_bookings ?? 0,
    bookings_by_status: raw.bookings_by_status ?? {},
    total_rooms: raw.total_rooms ?? 0,
    active_admin_users: raw.active_admin_users ?? 0,
    activity: raw.activity ?? [],
    health: raw.health ?? {
      database: "ok",
      api_latency_ms: 0,
      workers: "ok",
      status: "operational",
      label: "All Systems Operational",
    },
  };
}

export interface StripeInvoice {
  id: string;
  number?: string | null;
  status?: string | null;
  amount_due?: number | null;
  amount_paid?: number | null;
  currency?: string | null;
  hosted_invoice_url?: string | null;
  invoice_pdf?: string | null;
  created?: number | null;
}

export async function listBillingInvoices(tenantId: string): Promise<StripeInvoice[]> {
  const data = await fetchApi(
    `/admin/billing/invoices?tenant_id=${encodeURIComponent(tenantId)}`,
  );
  return (data.invoices as StripeInvoice[]) || [];
}

export async function createBillingRefund(payload: {
  tenant_id?: string;
  payment_intent_id?: string;
  charge_id?: string;
  amount_cents?: number;
  reason?: string;
}): Promise<unknown> {
  const data = await fetchApi("/admin/billing/refunds", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return data.refund;
}

export async function createBillingCredit(payload: {
  tenant_id: string;
  amount_cents: number;
  currency?: string;
  description?: string;
}): Promise<unknown> {
  const data = await fetchApi("/admin/billing/credits", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return data.credit;
}

export async function createBillingCoupon(payload: {
  code: string;
  percent_off?: number;
  amount_off_cents?: number;
  currency?: string;
  duration?: string;
}): Promise<unknown> {
  const data = await fetchApi("/admin/billing/coupons", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return data.coupon;
}

export async function applyBillingCoupon(payload: {
  tenant_id: string;
  code: string;
}): Promise<unknown> {
  const data = await fetchApi("/admin/billing/coupons/apply", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return data.result;
}

// ── Platform settings / audit / announcements / security ─────────────────────

export interface PlatformSettings {
  default_currency: string;
  supported_currencies: string[];
  tax_policy: {
    default_rate_percent: number;
    inclusive_pricing: boolean;
    tax_label: string;
    require_tax_id: boolean;
  };
  supported_languages: string[];
  default_language: string;
  integrations: {
    sendgrid: { api_key: string; from_email: string; from_name: string };
    twilio: {
      account_sid: string;
      auth_token: string;
      from_number: string;
      whatsapp_from?: string;
    };
    smtp?: {
      host: string;
      port: string;
      username: string;
      password: string;
      from_email: string;
      from_name: string;
      use_tls: boolean;
    };
    stripe: { publishable_key: string; secret_key: string; webhook_secret: string };
  };
}

export interface AuditLogEntry {
  id: string;
  timestamp: string;
  user_id: string | null;
  user_email: string | null;
  tenant_id: string | null;
  action_category: string;
  action: string;
  ip_address: string | null;
  status: "success" | "failure" | string;
  detail?: string | null;
  before?: Record<string, unknown> | string | null;
  after?: Record<string, unknown> | string | null;
  has_diff?: boolean;
}

export interface Announcement {
  id: string;
  title: string;
  body: string;
  channel: "banner" | "email" | "both";
  audience: string;
  status: "draft" | "published" | "archived";
  published_at?: string | null;
  scheduled_at?: string | null;
  expires_at?: string | null;
  regions?: string[];
  email_delivered_at?: string | null;
  created_by?: string | null;
  created_by_email?: string | null;
  created_at: string;
  updated_at: string;
}

export interface AnalyticsCohort {
  key: string;
  hotels: number;
  active: number;
  suspended: number;
  cancelled: number;
  churned: number;
  churn_rate: number;
  active_rate: number;
}

export interface SecurityPolicy {
  enforce_2fa: boolean;
  session_timeout_minutes: number;
  password_min_length: number;
  lockout_after_failures: number;
  maintenance_mode?: boolean;
  maintenance_message?: string;
  auto_backup_enabled?: boolean;
  auto_backup_interval_hours?: number;
  last_auto_backup_at?: string | null;
  offsite_backup_enabled?: boolean;
  offsite_backup_url?: string;
  offsite_backup_token?: string;
  offsite_backup_token_set?: boolean;
  ip_allowlist_enabled?: boolean;
  ip_allowlist?: string[];
}

export interface OpsJob {
  job_key: string;
  label: string;
  description: string;
  is_paused: boolean;
  last_run_at?: string | null;
  last_status?: string | null;
  last_detail?: string | null;
  updated_at?: string | null;
  updated_by?: string | null;
}

export interface OpsAlert {
  id: string;
  severity: string;
  category: string;
  title: string;
  detail?: string;
  is_acked: boolean;
  created_at: string;
  acked_at?: string | null;
  acked_by?: string | null;
}

export interface BackupLog {
  id: string;
  filename: string;
  size_bytes: number;
  status: "completed" | "failed" | string;
  created_by?: string | null;
  created_by_email?: string | null;
  created_at: string;
  downloaded_at?: string | null;
  error?: string | null;
}

export async function getPlatformSettings(): Promise<PlatformSettings> {
  const data = await fetchApi("/admin/platform-settings");
  return data.settings as PlatformSettings;
}

export async function updatePlatformSettings(
  payload: Partial<PlatformSettings>,
): Promise<PlatformSettings> {
  const data = await fetchApi("/admin/platform-settings", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
  return data.settings as PlatformSettings;
}

export async function listAuditLogs(params?: {
  limit?: number;
  offset?: number;
  category?: string;
  status?: string;
  q?: string;
}): Promise<{ logs: AuditLogEntry[]; total: number }> {
  const sp = new URLSearchParams();
  if (params?.limit != null) sp.set("limit", String(params.limit));
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.category) sp.set("category", params.category);
  if (params?.status) sp.set("status", params.status);
  if (params?.q) sp.set("q", params.q);
  const qs = sp.toString();
  const data = await fetchApi(`/admin/audit-logs${qs ? `?${qs}` : ""}`);
  return {
    logs: (data.logs as AuditLogEntry[]) || [],
    total: Number(data.total ?? 0),
  };
}

export async function exportAuditLogsCsv(params?: {
  category?: string;
  status?: string;
  q?: string;
}): Promise<void> {
  const sp = new URLSearchParams();
  if (params?.category) sp.set("category", params.category);
  if (params?.status) sp.set("status", params.status);
  if (params?.q) sp.set("q", params.q);
  const qs = sp.toString();
  const token = getAccessToken();
  const res = await fetch(`/api/v1/admin/audit-logs/export${qs ? `?${qs}` : ""}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    credentials: "include",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { message?: string }).message || `Export failed (${res.status})`);
  }
  const blob = await res.blob();
  const cd = res.headers.get("Content-Disposition") || "";
  const match = /filename="?([^"]+)"?/.exec(cd);
  const filename = match?.[1] || "platform-audit.csv";
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export async function listAnnouncements(): Promise<Announcement[]> {
  const data = await fetchApi("/admin/announcements");
  return (data.announcements as Announcement[]) || [];
}

export async function createAnnouncement(
  payload: Partial<Announcement> & { title: string },
): Promise<Announcement> {
  const data = await fetchApi("/admin/announcements", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return data.announcement as Announcement;
}

export async function updateAnnouncement(
  id: string,
  payload: Partial<Announcement> & { title: string },
): Promise<Announcement> {
  const data = await fetchApi(`/admin/announcements/${id}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
  return data.announcement as Announcement;
}

export async function deleteAnnouncement(id: string): Promise<void> {
  await fetchApi(`/admin/announcements/${id}`, { method: "DELETE" });
}

export async function getSecurityPolicy(): Promise<SecurityPolicy> {
  const data = await fetchApi("/admin/security-policy");
  return data.policy as SecurityPolicy;
}

export async function updateSecurityPolicy(
  payload: Partial<SecurityPolicy>,
): Promise<SecurityPolicy> {
  const data = await fetchApi("/admin/security-policy", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
  return data.policy as SecurityPolicy;
}

export async function listBackups(): Promise<BackupLog[]> {
  const data = await fetchApi("/admin/backups");
  return (data.backups as BackupLog[]) || [];
}

export async function createBackupSnapshot(): Promise<BackupLog> {
  const data = await fetchApi("/admin/backups/snapshot", { method: "POST" });
  return data.backup as BackupLog;
}

export async function restoreBackup(backupId: string): Promise<string> {
  const data = await fetchApi(`/admin/backups/${backupId}/restore`, {
    method: "POST",
    body: JSON.stringify({ confirm: "RESTORE" }),
  });
  return String(data.message ?? "Restore completed");
}

export function backupDownloadUrl(backupId: string): string {
  return `/api/v1/admin/backups/${backupId}/download`;
}

export async function getOpsHealth(): Promise<{
  status: string;
  database: string;
  open_alerts: number;
  critical_alerts: number;
  jobs: OpsJob[];
}> {
  const data = await fetchApi("/admin/ops/health");
  return data.health as {
    status: string;
    database: string;
    open_alerts: number;
    critical_alerts: number;
    jobs: OpsJob[];
  };
}

export async function listOpsAlerts(includeAcked = false): Promise<OpsAlert[]> {
  const q = includeAcked ? "?include_acked=true" : "";
  const data = await fetchApi(`/admin/ops/alerts${q}`);
  return (data.alerts as OpsAlert[]) || [];
}

export async function ackOpsAlert(alertId: string): Promise<void> {
  await fetchApi(`/admin/ops/alerts/${alertId}/ack`, { method: "POST" });
}

export async function listOpsJobs(): Promise<OpsJob[]> {
  const data = await fetchApi("/admin/ops/jobs");
  return (data.jobs as OpsJob[]) || [];
}

export async function setOpsJobPaused(jobKey: string, isPaused: boolean): Promise<void> {
  await fetchApi(`/admin/ops/jobs/${jobKey}`, {
    method: "PUT",
    body: JSON.stringify({ is_paused: isPaused }),
  });
}

export function tenantExportUrl(tenantId: string): string {
  return `/api/v1/admin/tenants/${tenantId}/export`;
}

export async function downloadTenantExport(tenantId: string): Promise<void> {
  const token = getAccessToken();
  const res = await fetch(tenantExportUrl(tenantId), {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    credentials: "include",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { message?: string }).message || `Export failed (${res.status})`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `tenant-export-${tenantId.slice(0, 8)}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

export async function downloadBackup(backupId: string, filename: string): Promise<void> {
  const token = getAccessToken();
  const res = await fetch(backupDownloadUrl(backupId), {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    credentials: "include",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { message?: string }).message || `Download failed (${res.status})`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// ── P2 growth: flags / usage / white-label / identity / compliance / partners ─

export interface FeatureFlag {
  key: string;
  label: string;
  description: string;
  enabled_global: boolean;
  created_at?: string;
  updated_at?: string;
  tenant_overrides: { tenant_id: string; enabled: boolean; updated_at?: string }[];
}

export interface UsageRow {
  tenant_id: string;
  hotel_name?: string | null;
  plan_code?: string | null;
  period_ym?: string;
  rooms: number;
  max_rooms: number;
  addon_rooms: number;
  rooms_cap: number | null;
  rooms_over_limit: boolean;
  staff: number;
  max_staff: number;
  addon_staff: number;
  staff_cap: number | null;
  staff_over_limit: boolean;
  api_calls?: number;
  api_calls_cap?: number | null;
  api_calls_over_limit?: boolean;
  ai_tokens?: number;
  ai_tokens_cap?: number | null;
  ai_tokens_over_limit?: boolean;
  storage_gb?: number;
  storage_gb_cap?: number | null;
  storage_gb_over_limit?: boolean;
}

export interface UsageAddon {
  id: string;
  code: string;
  name: string;
  metric: string;
  unit_price: string;
  included_units: number;
  is_active: boolean;
}

export interface TenantBranding {
  tenant_id: string;
  hotel_name?: string | null;
  brand_name?: string | null;
  logo_url?: string | null;
  primary_color?: string | null;
  custom_domain?: string | null;
  email_from_name?: string | null;
  email_from_address?: string | null;
  support_url?: string | null;
  dns_status?: string | null;
  dns_verified?: boolean;
  dns_checked_at?: string | null;
  dns_target?: string | null;
  updated_at?: string;
}

export interface TenantIdentity {
  tenant_id: string;
  sso_enabled: boolean;
  sso_protocol: string;
  issuer_url: string;
  client_id: string;
  client_secret: string;
  metadata_url: string;
  scim_enabled: boolean;
  scim_bearer_token?: string;
  scim_base_url?: string;
  sso_start_url?: string;
  scim_token_revealed?: boolean;
  updated_at?: string;
}

export interface IntegrationHealth {
  providers: Record<
    string,
    { status: string; detail?: string; configured?: boolean }
  >;
  recent_deliveries: {
    id: string;
    tenant_id?: string;
    trigger_key?: string;
    channel?: string;
    recipient?: string;
    status?: string;
    detail?: string;
    created_at?: string;
  }[];
}

export interface CompliancePolicy {
  retention_days: number;
  auto_purge_cancelled: boolean;
  dpa_contact_email: string;
  updated_at?: string;
}

export interface DsarRequest {
  id: string;
  tenant_id: string;
  hotel_name?: string | null;
  request_type: "export" | "erasure" | string;
  subject_email: string;
  status: string;
  notes: string;
  created_by_email?: string | null;
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
}

export interface Partner {
  id: string;
  name: string;
  contact_email: string;
  commission_percent: number;
  status: string;
  notes: string;
  created_at: string;
  updated_at: string;
  tenant_ids: string[];
  tenant_count: number;
}

export async function listFeatureFlags(): Promise<FeatureFlag[]> {
  const data = await fetchApi("/admin/feature-flags");
  return (data.flags as FeatureFlag[]) || [];
}

export async function updateFeatureFlag(
  key: string,
  payload: { enabled_global?: boolean; label?: string; description?: string },
): Promise<void> {
  await fetchApi(`/admin/feature-flags/${key}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function setFeatureFlagTenantOverride(
  key: string,
  tenantId: string,
  enabled: boolean,
): Promise<void> {
  await fetchApi(`/admin/feature-flags/${key}/tenants`, {
    method: "PUT",
    body: JSON.stringify({ tenant_id: tenantId, enabled }),
  });
}

export async function clearFeatureFlagTenantOverride(
  key: string,
  tenantId: string,
): Promise<void> {
  await fetchApi(`/admin/feature-flags/${key}/tenants/${tenantId}`, {
    method: "DELETE",
  });
}

export async function getUsageOverview(): Promise<{
  usage: UsageRow[];
  over_limit_count: number;
  addons: UsageAddon[];
}> {
  const data = await fetchApi("/admin/usage/overview");
  return {
    usage: (data.usage as UsageRow[]) || [],
    over_limit_count: Number(data.over_limit_count ?? 0),
    addons: (data.addons as UsageAddon[]) || [],
  };
}

export async function getTenantUsage(tenantId: string): Promise<{
  usage: UsageRow;
  assigned_addons: {
    id: string;
    quantity: number;
    code: string;
    name: string;
    metric: string;
    included_units: number;
    unit_price: string;
  }[];
}> {
  const data = await fetchApi(`/admin/usage/tenants/${tenantId}`);
  return {
    usage: data.usage as UsageRow,
    assigned_addons: (data.assigned_addons as {
      id: string;
      quantity: number;
      code: string;
      name: string;
      metric: string;
      included_units: number;
      unit_price: string;
    }[]) || [],
  };
}

export async function assignTenantAddon(
  tenantId: string,
  addonId: string,
  quantity = 1,
): Promise<void> {
  await fetchApi(`/admin/usage/tenants/${tenantId}/addons`, {
    method: "POST",
    body: JSON.stringify({ addon_id: addonId, quantity }),
  });
}

export async function removeTenantAddon(
  tenantId: string,
  assignmentId: string,
): Promise<void> {
  await fetchApi(`/admin/usage/tenants/${tenantId}/addons/${assignmentId}`, {
    method: "DELETE",
  });
}

export async function listWhiteLabel(): Promise<TenantBranding[]> {
  const data = await fetchApi("/admin/white-label");
  return (data.brands as TenantBranding[]) || [];
}

export async function getWhiteLabel(tenantId: string): Promise<TenantBranding> {
  const data = await fetchApi(`/admin/white-label/${tenantId}`);
  return data.branding as TenantBranding;
}

export async function upsertWhiteLabel(
  tenantId: string,
  payload: Partial<TenantBranding>,
): Promise<TenantBranding> {
  const data = await fetchApi(`/admin/white-label/${tenantId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
  return data.branding as TenantBranding;
}

export async function verifyWhiteLabelDns(tenantId: string): Promise<{
  dns: {
    domain: string;
    target: string;
    dns_status: string;
    dns_verified: boolean;
    dns_checked_at: string;
    detail: string;
  };
  branding: TenantBranding;
}> {
  const data = await fetchApi(`/admin/white-label/${tenantId}/verify-dns`, {
    method: "POST",
  });
  return data as {
    dns: {
      domain: string;
      target: string;
      dns_status: string;
      dns_verified: boolean;
      dns_checked_at: string;
      detail: string;
    };
    branding: TenantBranding;
  };
}

export async function listIdentityConfigs(): Promise<TenantIdentity[]> {
  const data = await fetchApi("/admin/identity");
  return (data.configs as TenantIdentity[]) || [];
}

export async function getIdentity(tenantId: string): Promise<TenantIdentity> {
  const data = await fetchApi(`/admin/identity/${tenantId}`);
  return data.identity as TenantIdentity;
}

export async function upsertIdentity(
  tenantId: string,
  payload: Partial<TenantIdentity>,
): Promise<TenantIdentity> {
  const data = await fetchApi(`/admin/identity/${tenantId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
  return data.identity as TenantIdentity;
}

export async function regenerateScimToken(tenantId: string): Promise<TenantIdentity> {
  const data = await fetchApi(`/admin/identity/${tenantId}/regenerate-scim-token`, {
    method: "POST",
  });
  return data.identity as TenantIdentity;
}

export async function getIntegrationsHealth(): Promise<IntegrationHealth> {
  const data = await fetchApi("/admin/integrations/health");
  return {
    providers: (data.providers as IntegrationHealth["providers"]) || {},
    recent_deliveries:
      (data.recent_deliveries as IntegrationHealth["recent_deliveries"]) || [],
  };
}

export async function testIntegration(
  provider: string,
  to?: string,
): Promise<{ ok?: boolean; detail?: string; status?: string; error?: string }> {
  const data = await fetchApi(`/admin/integrations/${provider}/test`, {
    method: "POST",
    body: JSON.stringify(to ? { to } : {}),
  });
  return data as { ok?: boolean; detail?: string; status?: string; error?: string };
}

export async function getCompliancePolicy(): Promise<CompliancePolicy> {
  const data = await fetchApi("/admin/compliance/policy");
  return data.policy as CompliancePolicy;
}

export async function updateCompliancePolicy(
  payload: Partial<CompliancePolicy>,
): Promise<CompliancePolicy> {
  const data = await fetchApi("/admin/compliance/policy", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
  return data.policy as CompliancePolicy;
}

export async function listDsarRequests(status?: string): Promise<DsarRequest[]> {
  const qs = status && status !== "all" ? `?status=${encodeURIComponent(status)}` : "";
  const data = await fetchApi(`/admin/compliance/dsar${qs}`);
  return (data.requests as DsarRequest[]) || [];
}

export async function createDsarRequest(payload: {
  tenant_id: string;
  request_type: string;
  subject_email: string;
  notes?: string;
  hotel_name?: string;
}): Promise<string> {
  const data = await fetchApi("/admin/compliance/dsar", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return String(data.id);
}

export async function updateDsarRequest(
  id: string,
  payload: { status?: string; notes?: string },
): Promise<void> {
  await fetchApi(`/admin/compliance/dsar/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function downloadDsarExport(requestId: string): Promise<void> {
  const token = getAccessToken();
  const res = await fetch(`/api/v1/admin/compliance/dsar/${requestId}/export`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    credentials: "include",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { message?: string }).message || `Export failed (${res.status})`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `dsar-${requestId.slice(0, 8)}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

export async function executeDsarErasure(requestId: string): Promise<void> {
  await fetchApi(`/admin/compliance/dsar/${requestId}/execute-erasure`, {
    method: "POST",
  });
}

export async function listPartners(): Promise<Partner[]> {
  const data = await fetchApi("/admin/partners");
  return (data.partners as Partner[]) || [];
}

export async function createPartner(payload: {
  name: string;
  contact_email: string;
  commission_percent?: number;
  notes?: string;
}): Promise<string> {
  const data = await fetchApi("/admin/partners", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return String(data.id);
}

export async function updatePartner(
  id: string,
  payload: Partial<{
    name: string;
    contact_email: string;
    commission_percent: number;
    status: string;
    notes: string;
  }>,
): Promise<void> {
  await fetchApi(`/admin/partners/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function linkPartnerTenant(partnerId: string, tenantId: string): Promise<void> {
  await fetchApi(`/admin/partners/${partnerId}/tenants/${tenantId}`, { method: "POST" });
}

export async function unlinkPartnerTenant(partnerId: string, tenantId: string): Promise<void> {
  await fetchApi(`/admin/partners/${partnerId}/tenants/${tenantId}`, { method: "DELETE" });
}

export async function provisionPartnerUser(partnerId: string): Promise<{
  user_id: string;
  email: string;
}> {
  const data = await fetchApi(`/admin/partners/${partnerId}/provision-user`, {
    method: "POST",
  });
  return { user_id: String(data.user_id), email: String(data.email) };
}

export async function getPartnerMe(): Promise<{
  partner: Partner;
  stats: { tenant_count: number; active_hotels: number; commission_percent: number };
}> {
  const data = await fetchApi("/partner/me");
  return {
    partner: data.partner as Partner,
    stats: data.stats as {
      tenant_count: number;
      active_hotels: number;
      commission_percent: number;
    },
  };
}

export async function getPartnerTenants(): Promise<HotelTenant[]> {
  const data = await fetchApi("/partner/tenants");
  return (data.tenants as HotelTenant[]) || [];
}

export async function getPartnerAnalytics(): Promise<{
  tenant_count: number;
  by_plan: Record<string, number>;
  by_country: Record<string, number>;
  by_status: Record<string, number>;
  estimated_commission_share_percent: number;
}> {
  const data = await fetchApi("/partner/analytics");
  return data.analytics as {
    tenant_count: number;
    by_plan: Record<string, number>;
    by_country: Record<string, number>;
    by_status: Record<string, number>;
    estimated_commission_share_percent: number;
  };
}

// ── API / webhook platform admin (#25) ───────────────────────────────────────

export interface ApiPlatformOverview {
  keys_active: number;
  keys_total: number;
  webhooks_active: number;
  webhooks_failing: number;
  deliveries_failed_today: number;
  event_catalog: string[];
}

export interface ApiRateLimitPolicy {
  default_per_min: number;
  burst_per_min: number;
  platform_per_min: number;
  updated_at?: string | null;
  updated_by?: string | null;
}

export interface PlatformApiKey {
  id: string;
  tenant_id?: string | null;
  hotel_name?: string | null;
  name: string;
  key_prefix: string;
  rate_limit_per_min: number;
  scopes: string[];
  is_active: boolean;
  last_used_at?: string | null;
  created_at: string;
  created_by?: string | null;
  revoked_at?: string | null;
}

export interface PlatformWebhook {
  id: string;
  tenant_id?: string | null;
  hotel_name?: string | null;
  name: string;
  url: string;
  secret: string;
  secret_configured: boolean;
  events: string[];
  is_active: boolean;
  failure_count: number;
  last_success_at?: string | null;
  last_failure_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface WebhookDelivery {
  id: string;
  endpoint_id: string;
  tenant_id?: string | null;
  event_type: string;
  status: string;
  attempt: number;
  http_status?: number | null;
  error?: string | null;
  duration_ms?: number | null;
  created_at: string;
  request_body?: string;
}

export async function getApiPlatformOverview(): Promise<{
  overview: ApiPlatformOverview;
  rate_limits: ApiRateLimitPolicy;
}> {
  const data = await fetchApi("/admin/api-platform/overview");
  return {
    overview: data.overview as ApiPlatformOverview,
    rate_limits: data.rate_limits as ApiRateLimitPolicy,
  };
}

export async function updateApiRateLimits(
  payload: Partial<ApiRateLimitPolicy>,
): Promise<ApiRateLimitPolicy> {
  const data = await fetchApi("/admin/api-platform/rate-limits", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
  return data.policy as ApiRateLimitPolicy;
}

export async function listPlatformApiKeys(tenantId?: string): Promise<PlatformApiKey[]> {
  const qs = tenantId ? `?tenant_id=${encodeURIComponent(tenantId)}` : "";
  const data = await fetchApi(`/admin/api-platform/keys${qs}`);
  return (data.keys as PlatformApiKey[]) || [];
}

export async function createPlatformApiKey(payload: {
  name: string;
  tenant_id?: string | null;
  rate_limit_per_min?: number;
  scopes?: string[];
}): Promise<{ key: PlatformApiKey; secret: string }> {
  const data = await fetchApi("/admin/api-platform/keys", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { key: data.key as PlatformApiKey, secret: String(data.secret) };
}

export async function updatePlatformApiKey(
  id: string,
  payload: Partial<{ name: string; rate_limit_per_min: number; is_active: boolean; scopes: string[] }>,
): Promise<PlatformApiKey> {
  const data = await fetchApi(`/admin/api-platform/keys/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
  return data.key as PlatformApiKey;
}

export async function revokePlatformApiKey(id: string): Promise<void> {
  await fetchApi(`/admin/api-platform/keys/${id}/revoke`, { method: "POST" });
}

export async function listPlatformWebhooks(tenantId?: string): Promise<PlatformWebhook[]> {
  const qs = tenantId ? `?tenant_id=${encodeURIComponent(tenantId)}` : "";
  const data = await fetchApi(`/admin/api-platform/webhooks${qs}`);
  return (data.webhooks as PlatformWebhook[]) || [];
}

export async function createPlatformWebhook(payload: {
  name: string;
  url: string;
  tenant_id?: string | null;
  events?: string[];
}): Promise<{ webhook: PlatformWebhook; secret: string }> {
  const data = await fetchApi("/admin/api-platform/webhooks", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { webhook: data.webhook as PlatformWebhook, secret: String(data.secret || "") };
}

export async function updatePlatformWebhook(
  id: string,
  payload: Partial<{ name: string; url: string; events: string[]; is_active: boolean }>,
): Promise<PlatformWebhook> {
  const data = await fetchApi(`/admin/api-platform/webhooks/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
  return data.webhook as PlatformWebhook;
}

export async function deletePlatformWebhook(id: string): Promise<void> {
  await fetchApi(`/admin/api-platform/webhooks/${id}`, { method: "DELETE" });
}

export async function testPlatformWebhook(id: string): Promise<{
  ok: boolean;
  status: string;
  http_status?: number | null;
  error?: string | null;
}> {
  const data = await fetchApi(`/admin/api-platform/webhooks/${id}/test`, { method: "POST" });
  return data.delivery as {
    ok: boolean;
    status: string;
    http_status?: number | null;
    error?: string | null;
  };
}

export async function listWebhookDeliveries(params?: {
  endpoint_id?: string;
  status?: string;
}): Promise<WebhookDelivery[]> {
  const sp = new URLSearchParams();
  if (params?.endpoint_id) sp.set("endpoint_id", params.endpoint_id);
  if (params?.status) sp.set("status", params.status);
  const qs = sp.toString();
  const data = await fetchApi(`/admin/api-platform/deliveries${qs ? `?${qs}` : ""}`);
  return (data.deliveries as WebhookDelivery[]) || [];
}
