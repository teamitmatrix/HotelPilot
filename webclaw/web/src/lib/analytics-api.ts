import { API_BASE, fetchApi } from "./api";
import { getAccessToken } from "./auth";
import { getActiveTenantId } from "./active-tenant";

export type AnalyticsKpis = {
  room_revenue: string;
  rooms_available: number;
  available_room_nights: number;
  rooms_sold: number;
  occupancy_pct: string;
  adr: string;
  revpar: string;
  alos: string;
  cancellation_rate_pct: string;
  cancellations: number;
  reservations_created: number;
};

export type AnalyticsDaily = {
  date: string;
  room_revenue: string;
  rooms_sold: number;
  rooms_available: number;
  occupancy_pct: string;
};

export type BookingSource = {
  key: string;
  label: string;
  bookings: number;
  room_nights: number;
  revenue: string;
  share_pct: string;
};

export type BranchRow = {
  property_id: string;
  hotel_id: string | null;
  hotel_name: string;
  currency: string;
  room_revenue: string;
  occupancy_pct: string;
  adr: string;
  revpar: string;
  alos: string;
  rooms_available: number;
  rooms_sold: number;
  cancellation_rate_pct: string;
};

export type PerformancePayload = {
  status: string;
  hotel: {
    hotel_id: string | null;
    hotel_name: string;
    currency: string;
    timezone?: string;
  };
  period: { from_date: string; to_date: string; label: string };
  kpis: AnalyticsKpis;
  revenue_trend: {
    current_month: { label: string; daily: AnalyticsDaily[]; room_revenue: string };
    previous_month: { label: string; daily: AnalyticsDaily[]; room_revenue: string };
    last_year: { label: string; daily: AnalyticsDaily[]; room_revenue: string };
    aligned: Array<{
      day: number;
      current?: number;
      previous_month?: number;
      last_year?: number;
    }>;
  };
  booking_sources: {
    sources: BookingSource[];
    ota_breakdown: { label: string; bookings: number; revenue: string }[];
  };
  branches: BranchRow[];
  occupancy_table: AnalyticsDaily[];
  generated_at: string;
};

function qs(fromDate?: string, toDate?: string) {
  const params = new URLSearchParams();
  if (fromDate) params.set("from_date", fromDate);
  if (toDate) params.set("to_date", toDate);
  const q = params.toString();
  return q ? `?${q}` : "";
}

export async function getPerformanceAnalytics(fromDate?: string, toDate?: string) {
  const data = await fetchApi(`/analytics/performance${qs(fromDate, toDate)}`);
  return data as unknown as PerformancePayload;
}

export type ExpectedOccupancyDay = {
  date: string;
  rooms_sold: number;
  rooms_available: number;
  rooms_vacant: number;
  occupancy_pct: string;
  arrivals: number;
  departures: number;
};

export type ExpectedOccupancyPayload = {
  status: string;
  hotel: {
    hotel_id: string | null;
    hotel_name: string;
    currency: string;
  };
  period: { from_date: string; to_date: string; days: number };
  summary: {
    rooms_available: number;
    room_nights_sold: number;
    available_room_nights: number;
    avg_occupancy_pct: string;
    peak_occupancy_pct: string;
  };
  daily: ExpectedOccupancyDay[];
};

export async function getExpectedOccupancy(days = 14, fromDate?: string) {
  const params = new URLSearchParams();
  params.set("days", String(days));
  if (fromDate) params.set("from_date", fromDate);
  const data = await fetchApi(`/analytics/expected-occupancy?${params.toString()}`);
  return data as unknown as ExpectedOccupancyPayload;
}

async function downloadExport(path: string, filename: string) {
  const token = getAccessToken();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  const tenantId = getActiveTenantId();
  if (tenantId) {
    headers["X-Property-ID"] = tenantId;
    if (tenantId.toUpperCase() !== "ALL") headers["X-Tenant-Id"] = tenantId;
  }
  const res = await fetch(`${API_BASE}${path}`, { headers, credentials: "include" });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { message?: string }).message || `Export failed (${res.status})`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export async function exportAnalyticsCsv(fromDate?: string, toDate?: string) {
  const suffix = qs(fromDate, toDate);
  await downloadExport(
    `/analytics/performance/export.csv${suffix}`,
    `hotel-analytics${fromDate ? `-${fromDate}` : ""}.csv`,
  );
}

export async function exportAnalyticsPdf(fromDate?: string, toDate?: string) {
  const suffix = qs(fromDate, toDate);
  await downloadExport(
    `/analytics/performance/export.pdf${suffix}`,
    `hotel-analytics${fromDate ? `-${fromDate}` : ""}.pdf`,
  );
}
