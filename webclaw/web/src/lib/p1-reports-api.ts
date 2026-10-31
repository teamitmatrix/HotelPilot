import { fetchApi } from "./api";

export async function getChannelProduction(fromDate?: string, toDate?: string) {
  const qs = new URLSearchParams();
  if (fromDate) qs.set("from_date", fromDate);
  if (toDate) qs.set("to_date", toDate);
  const q = qs.toString();
  return fetchApi(`/analytics/channel-production${q ? `?${q}` : ""}`);
}

export async function getChannelCommission(fromDate?: string, toDate?: string) {
  const qs = new URLSearchParams();
  if (fromDate) qs.set("from_date", fromDate);
  if (toDate) qs.set("to_date", toDate);
  const q = qs.toString();
  return fetchApi(`/analytics/channel-commission${q ? `?${q}` : ""}`);
}

export async function getPickupPace(asOf?: string, days = 30) {
  const qs = new URLSearchParams();
  qs.set("days", String(days));
  if (asOf) qs.set("as_of", asOf);
  return fetchApi(`/analytics/pickup-pace?${qs}`);
}

export async function getForecast(days = 30, fromDate?: string) {
  const qs = new URLSearchParams();
  qs.set("days", String(days));
  if (fromDate) qs.set("from_date", fromDate);
  return fetchApi(`/analytics/forecast?${qs}`);
}

export async function getCancellationsBySource(fromDate?: string, toDate?: string) {
  const qs = new URLSearchParams();
  if (fromDate) qs.set("from_date", fromDate);
  if (toDate) qs.set("to_date", toDate);
  const q = qs.toString();
  return fetchApi(`/analytics/cancellations-by-source${q ? `?${q}` : ""}`);
}
