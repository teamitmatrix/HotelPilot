import { fetchApi, postAction } from "./api";

export interface NightlyRate {
  date: string;
  rate: string;
  source: "base" | "rate_plan" | string;
  rate_plan_id?: string | null;
  rate_plan_name?: string | null;
  rate_plan_code?: string | null;
}

export interface RateCalendar {
  hotel_id: string;
  room_type_id: string;
  room_type_name: string;
  room_type_code: string;
  base_rate: string;
  start_date: string;
  end_date: string;
  nights: number;
  nightly_rates: NightlyRate[];
  total: string;
  average_nightly: string;
}

export interface RateWindowResult {
  rate_plan_id: string;
  updated: boolean;
  name: string;
  code: string;
  start_date: string;
  end_date: string;
  nightly_rate: string;
  priority: number;
}

export async function fetchRateCalendar(params: {
  hotel_id: string;
  room_type_id: string;
  start_date: string;
  end_date: string;
}): Promise<RateCalendar> {
  const q = new URLSearchParams(params);
  const data = await fetchApi(`/hospitalityclaw/rate-calendar?${q.toString()}`);
  return data as RateCalendar;
}

export async function quoteStay(params: {
  hotel_id: string;
  room_type_id: string;
  check_in: string;
  check_out: string;
}): Promise<RateCalendar> {
  const q = new URLSearchParams(params);
  const data = await fetchApi(`/hospitalityclaw/rate-calendar?${q.toString()}`);
  return data as RateCalendar;
}

export async function setRateWindow(body: {
  hotel_id: string;
  room_type_id?: string;
  start_date: string;
  end_date: string;
  nightly_rate: string;
  name?: string;
  notes?: string;
  id?: string;
}): Promise<RateWindowResult> {
  const data = await postAction("hospitalityclaw", "set-rate-window", body);
  return data as RateWindowResult;
}
