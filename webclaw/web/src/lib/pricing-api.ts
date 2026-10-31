import { fetchApi } from "./api";

export type PricingRule = {
  id: string;
  hotel_id: string;
  room_type_id: string | null;
  room_type_name?: string | null;
  name: string;
  rule_kind: "occupancy" | "min_los" | string;
  priority: number;
  is_active: boolean;
  occupancy_op?: string | null;
  occupancy_pct?: string | null;
  within_hours?: number | null;
  adjustment_pct?: string | null;
  min_nights?: number | null;
  days_of_week?: number[];
  notes?: string | null;
  summary?: string;
  created_at?: string;
  updated_at?: string;
};

export type PricingDashboard = {
  horizon_days: number;
  hotels: { id: string; name: string; currency?: string }[];
  room_types: { id: string; hotel_id: string; name: string; code: string; base_rate: string }[];
  rules: PricingRule[];
  recent_runs: {
    id: string;
    hotel_id: string;
    business_date: string;
    status: string;
    summary: Record<string, unknown>;
    created_at: string;
  }[];
  yield_rates: {
    id: string;
    hotel_id: string;
    room_type_id: string | null;
    name: string;
    code: string;
    start_date: string;
    end_date: string;
    nightly_rate: string;
    priority: number;
    notes?: string | null;
  }[];
  stay_restrictions: {
    id: string;
    hotel_id: string;
    room_type_id: string | null;
    stay_date: string;
    min_nights: number;
    source_rule_id?: string | null;
  }[];
};

export async function getPricingDashboard(): Promise<PricingDashboard> {
  const data = await fetchApi("/hotel/pricing/rules");
  return data as unknown as PricingDashboard;
}

export async function savePricingRule(body: Record<string, unknown>) {
  const data = await fetchApi("/hotel/pricing/rules", {
    method: "PUT",
    body: JSON.stringify(body),
  });
  return data.rule as PricingRule;
}

export async function togglePricingRule(ruleId: string, isActive: boolean) {
  const data = await fetchApi(`/hotel/pricing/rules/${ruleId}`, {
    method: "PATCH",
    body: JSON.stringify({ is_active: isActive }),
  });
  return data.rule as PricingRule;
}

export async function deletePricingRule(ruleId: string) {
  await fetchApi(`/hotel/pricing/rules/${ruleId}`, { method: "DELETE" });
}

export async function evaluatePricing(hotelId?: string) {
  return fetchApi("/hotel/pricing/evaluate", {
    method: "POST",
    body: JSON.stringify(hotelId ? { hotel_id: hotelId } : {}),
  });
}
