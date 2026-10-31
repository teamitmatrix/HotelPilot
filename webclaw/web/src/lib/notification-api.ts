import { fetchApi } from "./api";

export type NotifyChannelFlags = {
  email: boolean;
  sms: boolean;
  whatsapp: boolean;
};

export type TriggerConfig = {
  enabled: boolean;
  channels: NotifyChannelFlags;
  email_subject: string;
  email_body: string;
  sms_body: string;
  whatsapp_body: string;
};

export type NotificationSettings = {
  booking_confirmation: TriggerConfig;
  pre_arrival: TriggerConfig;
  folio_receipt: TriggerConfig;
};

export type TriggerMeta = {
  key: string;
  label: string;
  description: string;
  placeholders: string[];
  config: TriggerConfig;
};

export async function getNotificationSettings(): Promise<{
  settings: NotificationSettings;
  triggers: TriggerMeta[];
  providers: { email: boolean; sms: boolean; whatsapp: boolean };
}> {
  const data = await fetchApi("/hotel/notifications/settings");
  return {
    settings: data.settings as NotificationSettings,
    triggers: (data.triggers as TriggerMeta[]) || [],
    providers: (data.providers as { email: boolean; sms: boolean; whatsapp: boolean }) || {
      email: false,
      sms: false,
      whatsapp: false,
    },
  };
}

export async function saveNotificationSettings(
  settings: Partial<NotificationSettings>,
): Promise<NotificationSettings> {
  const data = await fetchApi("/hotel/notifications/settings", {
    method: "PUT",
    body: JSON.stringify(settings),
  });
  if (data.status !== "ok") {
    throw new Error((data.message as string) || "Save failed");
  }
  return data.settings as NotificationSettings;
}

export async function getNotificationLogs(limit = 30) {
  const data = await fetchApi(`/hotel/notifications/logs?limit=${limit}`);
  return (data.logs as Array<Record<string, unknown>>) || [];
}

export async function runPreArrivalJob() {
  return fetchApi("/hotel/notifications/run-pre-arrival", { method: "POST", body: "{}" });
}
