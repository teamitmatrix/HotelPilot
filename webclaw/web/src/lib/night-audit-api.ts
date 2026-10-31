import { API_BASE, fetchApi } from "./api";
import { getAccessToken } from "./auth";
import { getActiveTenantId } from "./active-tenant";

export type NightAuditKpis = {
  room_revenue?: string;
  tax_liabilities?: string;
  outstanding_folio_balances?: string;
  rooms_occupied?: number;
  rooms_available?: number;
  occupancy_rate_pct?: string;
  revpar?: string;
};

export type NightAuditException = {
  code: string;
  severity: string;
  title: string;
  message: string;
  folio_id?: string | null;
  reservation_id?: string | null;
  room_number?: string | null;
  balance?: string | null;
  href?: string | null;
};

export type NightAuditRun = {
  id: string;
  hotel_id: string;
  business_date: string;
  status: string;
  triggered_by?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  kpis?: NightAuditKpis | null;
  exceptions?: NightAuditException[];
  exception_count?: number;
  summary?: Record<string, unknown> | null;
  has_pdf?: boolean;
};

export async function listNightAuditRuns(limit = 50): Promise<NightAuditRun[]> {
  const data = await fetchApi(`/hotel/night-audit/latest?limit=${limit}`);
  return Array.isArray(data.runs) ? (data.runs as NightAuditRun[]) : [];
}

export async function getNightAuditExceptions(businessDate?: string): Promise<{
  business_date: string | null;
  exceptions: NightAuditException[];
  exception_count: number;
}> {
  const qs = businessDate
    ? `?business_date=${encodeURIComponent(businessDate)}`
    : "";
  const data = await fetchApi(`/hotel/night-audit/exceptions${qs}`);
  return {
    business_date:
      data.business_date != null ? String(data.business_date) : null,
    exceptions: Array.isArray(data.exceptions)
      ? (data.exceptions as NightAuditException[])
      : [],
    exception_count: Number(data.exception_count || 0),
  };
}

export async function downloadNightAuditPdf(runId: string, businessDate?: string) {
  const token = getAccessToken();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  const tenantId = getActiveTenantId();
  if (tenantId) {
    headers["X-Property-ID"] = tenantId;
    if (tenantId.toUpperCase() !== "ALL") headers["X-Tenant-Id"] = tenantId;
  }
  const res = await fetch(`${API_BASE}/hotel/night-audit/runs/${runId}/pdf`, {
    headers,
    credentials: "include",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { message?: string }).message || `PDF download failed (${res.status})`,
    );
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `night-audit-${businessDate || runId}.pdf`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
