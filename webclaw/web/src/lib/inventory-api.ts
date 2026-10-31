import { fetchApi } from "./api";

export type AvailabilityCell = {
  room_type_id: string;
  date: string;
  physical: number;
  assigned: number;
  unassigned: number;
  availability: number;
  sellable: number;
  stop_sell: boolean;
  closed_to_arrival: boolean;
  closed_to_departure: boolean;
  min_nights: number;
  max_nights: number | null;
};

export type AvailabilityGrid = {
  hotel_id: string;
  hotel_name: string;
  start_date: string;
  end_date: string;
  accepting_bookings: boolean;
  room_types: { id: string; name: string; code: string; physical_rooms: number }[];
  dates: string[];
  cells: AvailabilityCell[];
};

export type InventoryControl = {
  id: string;
  hotel_id: string;
  room_type_id: string | null;
  stay_date: string;
  stop_sell: boolean;
  closed_to_arrival: boolean;
  closed_to_departure: boolean;
  min_nights: number;
  max_nights: number | null;
  notes: string;
  updated_at?: string;
};

export async function fetchAvailabilityGrid(params: {
  hotel_id: string;
  start_date: string;
  end_date: string;
}): Promise<AvailabilityGrid> {
  const qs = new URLSearchParams(params);
  const data = await fetchApi(`/hotel/inventory/availability?${qs}`);
  return data as unknown as AvailabilityGrid;
}

export async function fetchInventoryRestrictions(params: {
  hotel_id: string;
  start_date: string;
  end_date: string;
  room_type_id?: string;
}): Promise<InventoryControl[]> {
  const qs = new URLSearchParams({
    hotel_id: params.hotel_id,
    start_date: params.start_date,
    end_date: params.end_date,
  });
  if (params.room_type_id) qs.set("room_type_id", params.room_type_id);
  const data = await fetchApi(`/hotel/inventory/restrictions?${qs}`);
  return (data.controls || []) as InventoryControl[];
}

export async function saveInventoryRestrictions(body: Record<string, unknown>) {
  return fetchApi("/hotel/inventory/restrictions", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function clearInventoryRestrictions(body: Record<string, unknown>) {
  return fetchApi("/hotel/inventory/restrictions/clear", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export type RestrictionCalendarCell = {
  room_type_id: string;
  rate_plan_id?: string | null;
  ota?: string | null;
  stay_date: string;
  stop_sell: boolean;
  closed_to_arrival: boolean;
  closed_to_departure: boolean;
  min_nights: number;
  max_nights: number | null;
};

export type RestrictionCalendar = {
  hotel_id: string;
  hotel_name: string;
  start_date: string;
  end_date: string;
  room_types: { id: string; name: string; code: string }[];
  rate_plans: { id: string; name: string; code: string }[];
  dates: string[];
  cells: RestrictionCalendarCell[];
};

export async function fetchRestrictionCalendar(params: {
  hotel_id: string;
  start_date: string;
  end_date: string;
  room_type_id?: string;
  rate_plan_id?: string;
  ota?: string;
}): Promise<RestrictionCalendar> {
  const qs = new URLSearchParams({
    hotel_id: params.hotel_id,
    start_date: params.start_date,
    end_date: params.end_date,
  });
  if (params.room_type_id) qs.set("room_type_id", params.room_type_id);
  if (params.rate_plan_id) qs.set("rate_plan_id", params.rate_plan_id);
  if (params.ota) qs.set("ota", params.ota);
  const data = await fetchApi(`/hotel/inventory/restriction-calendar?${qs}`);
  return data as unknown as RestrictionCalendar;
}
