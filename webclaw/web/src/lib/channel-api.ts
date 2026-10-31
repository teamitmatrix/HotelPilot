import { fetchApi } from "./api";

export type ChannelConnection = {
  id: string;
  hotel_id: string;
  property_id: string;
  ota: string;
  ota_label: string;
  provider: string;
  status: string;
  api_base_url: string;
  property_external_id: string;
  api_key_configured: boolean;
  api_key_last4: string;
  webhook_secret_configured: boolean;
  commission_percent?: number;
  last_success_at: string | null;
  last_error: string | null;
};

export type ChannelAllotment = {
  id: string;
  hotel_id: string;
  ota: string;
  ota_label: string;
  room_type_id: string;
  stay_date: string;
  allotment: number;
  notes?: string;
};

export type ChannelMapping = {
  id: string;
  hotel_id: string;
  property_id: string;
  ota: string;
  ota_label: string;
  ota_room_type_code: string;
  room_type_id: string;
  room_type_name: string;
  room_type_code: string;
  room_numbers: string[];
};

export type ChannelLog = {
  id: string;
  hotel_id: string | null;
  ota: string | null;
  ota_label: string;
  direction: string;
  kind: string;
  status: string;
  message: string;
  created_at: string;
};

export type ChannelDashboard = {
  webhook_path: string;
  otas: { code: string; label: string }[];
  providers: string[];
  hotels: { id: string; name: string; currency?: string }[];
  connections: ChannelConnection[];
  mappings: ChannelMapping[];
  room_types: { id: string; hotel_id: string; name: string; code: string; base_rate: string }[];
  logs: ChannelLog[];
  errors: ChannelLog[];
};

export async function getChannelDashboard(): Promise<ChannelDashboard> {
  const data = await fetchApi("/hotel/channels");
  return data as unknown as ChannelDashboard;
}

export async function saveChannelConnection(body: Record<string, string>) {
  const data = await fetchApi("/hotel/channels/connections", {
    method: "PUT",
    body: JSON.stringify(body),
  });
  return data.connection as ChannelConnection;
}

export async function saveChannelMapping(body: Record<string, string>) {
  const data = await fetchApi("/hotel/channels/mappings", {
    method: "PUT",
    body: JSON.stringify(body),
  });
  return data.mapping as ChannelMapping;
}

export async function deleteChannelMapping(mappingId: string) {
  await fetchApi(`/hotel/channels/mappings/${mappingId}`, { method: "DELETE" });
}

export async function syncChannels(hotelId?: string, ota?: string) {
  const body: Record<string, string> = {};
  if (hotelId) body.hotel_id = hotelId;
  if (ota) body.ota = ota;
  return fetchApi("/hotel/channels/sync", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function fetchChannelAllotments(params: {
  hotel_id: string;
  start_date: string;
  end_date: string;
  ota?: string;
  room_type_id?: string;
}): Promise<ChannelAllotment[]> {
  const qs = new URLSearchParams({
    hotel_id: params.hotel_id,
    start_date: params.start_date,
    end_date: params.end_date,
  });
  if (params.ota) qs.set("ota", params.ota);
  if (params.room_type_id) qs.set("room_type_id", params.room_type_id);
  const data = await fetchApi(`/hotel/channels/allotments?${qs}`);
  return (data.allotments || []) as ChannelAllotment[];
}

export async function saveChannelAllotments(body: Record<string, unknown>) {
  return fetchApi("/hotel/channels/allotments", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}
