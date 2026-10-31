/** Owner portfolio / multi-branch helpers. */

import { fetchApi, type ApiResponse } from "./api";

export interface OwnerProperty {
  property_id: string;
  company_id?: string;
  hotel_name: string;
  timezone?: string | null;
  country?: string | null;
  currency?: string | null;
  status?: string | null;
  tenant_status?: string | null;
  accepting_bookings?: boolean;
  admin_email?: string | null;
  group_id?: string | null;
  address?: string | null;
}

export interface BranchMetrics {
  property_id: string;
  hotel_name: string;
  rooms: number;
  occupied: number;
  occupancy_pct: number;
  guests: number;
  reservations: number;
  revenue: number;
  monthly_revenue: number;
  staff: number | null;
  currency: string;
}

export interface PortfolioSnapshot {
  group_id: string | null;
  properties: OwnerProperty[];
  branches: BranchMetrics[];
  totals: {
    revenue: number;
    rooms: number;
    guests: number;
    occupancy_pct: number;
    monthly_revenue: number;
  };
  currency: string;
}

function propertyHeaders(propertyId: string): Record<string, string> {
  return {
    "X-Property-ID": propertyId,
    "X-Tenant-Id": propertyId,
  };
}

function num(v: unknown): number {
  const n = typeof v === "string" ? parseFloat(v) : Number(v);
  return Number.isFinite(n) ? n : 0;
}

function monthStartIso(): string {
  const now = new Date();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  return `${now.getFullYear()}-${m}-01`;
}

export async function createOwnerProperty(body: {
  name: string;
  address?: string;
  timezone?: string;
  currency?: string;
  country?: string;
  tax_template?: string;
}): Promise<ApiResponse> {
  return fetchApi("/owner/properties", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function updateOwnerPropertyStatus(
  propertyId: string,
  status: "active" | "closed",
): Promise<ApiResponse> {
  return fetchApi(`/owner/properties/${propertyId}/status`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
}

export async function fetchOwnerProperties(): Promise<{
  group_id: string | null;
  properties: OwnerProperty[];
}> {
  try {
    const data = await fetchApi("/owner/properties");
    if (data.status !== "ok") {
      return { group_id: null, properties: [] };
    }
    const props = Array.isArray(data.properties)
      ? (data.properties as OwnerProperty[])
      : [];
    return {
      group_id: (data.group_id as string) || null,
      properties: props,
    };
  } catch {
    return { group_id: null, properties: [] };
  }
}

async function fetchBranchMetrics(prop: OwnerProperty): Promise<BranchMetrics> {
  const headers = propertyHeaders(prop.property_id);
  const fromDate = monthStartIso();

  const [status, roomsRes, revenueRes, staffRes] = await Promise.all([
    fetchApi("/hospitalityclaw/status", { headers }).catch(() => ({ status: "error" }) as ApiResponse),
    fetchApi("/hospitalityclaw/list-rooms?limit=500", { headers }).catch(
      () => ({ status: "error" }) as ApiResponse,
    ),
    fetchApi(`/hospitalityclaw/list-revenue?limit=1&from_date=${fromDate}`, {
      headers,
    }).catch(() => ({ status: "error" }) as ApiResponse),
    fetchApi("/hotel/users", { headers }).catch(() => ({ status: "error" }) as ApiResponse),
  ]);

  const roomsList = Array.isArray(roomsRes.rooms)
    ? (roomsRes.rooms as { status?: string }[])
    : [];
  const roomCount =
    roomsList.length ||
    num(roomsRes.total_count) ||
    (status.status === "ok" ? num(status.rooms) : 0);
  const occupied =
    roomsList.length > 0
      ? roomsList.filter((r) => r.status === "occupied").length
      : 0;
  const occupancy_pct =
    roomCount > 0 ? Math.round((occupied / roomCount) * 1000) / 10 : 0;

  let staff: number | null = null;
  if (staffRes.status === "ok") {
    if (typeof staffRes.total_count === "number") staff = staffRes.total_count;
    else if (Array.isArray(staffRes.users)) staff = staffRes.users.length;
  }

  const revenue =
    status.status === "ok"
      ? num(status.revenue)
      : num(revenueRes.revenue_total);
  const monthly_revenue = num(revenueRes.revenue_total);
  const currency =
    String(status.currency || revenueRes.currency || prop.currency || "USD");

  return {
    property_id: prop.property_id,
    hotel_name: prop.hotel_name || "Property",
    rooms: roomCount,
    occupied,
    occupancy_pct,
    guests: status.status === "ok" ? num(status.guests) : 0,
    reservations: status.status === "ok" ? num(status.reservations) : 0,
    revenue,
    monthly_revenue,
    staff,
    currency,
  };
}

export async function loadPortfolioSnapshot(): Promise<PortfolioSnapshot> {
  const { group_id, properties } = await fetchOwnerProperties();
  if (properties.length === 0) {
    return {
      group_id,
      properties: [],
      branches: [],
      totals: {
        revenue: 0,
        rooms: 0,
        guests: 0,
        occupancy_pct: 0,
        monthly_revenue: 0,
      },
      currency: "USD",
    };
  }

  const branches = await Promise.all(properties.map(fetchBranchMetrics));
  const totalRooms = branches.reduce((s, b) => s + b.rooms, 0);
  const totalOccupied = branches.reduce((s, b) => s + b.occupied, 0);
  const totals = {
    revenue: branches.reduce((s, b) => s + b.revenue, 0),
    rooms: totalRooms,
    guests: branches.reduce((s, b) => s + b.guests, 0),
    occupancy_pct:
      totalRooms > 0 ? Math.round((totalOccupied / totalRooms) * 1000) / 10 : 0,
    monthly_revenue: branches.reduce((s, b) => s + b.monthly_revenue, 0),
  };
  const currency = branches.find((b) => b.currency)?.currency || "USD";

  return { group_id, properties, branches, totals, currency };
}

export function formatPortfolioMoney(amount: number, currency = "USD"): string {
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      maximumFractionDigits: amount >= 1000 ? 0 : 2,
    }).format(amount);
  } catch {
    return `$${amount.toFixed(amount >= 1000 ? 0 : 2)}`;
  }
}
