"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/toast-provider";
import {
  getNotificationLogs,
  getNotificationSettings,
  saveNotificationSettings,
  type NotificationSettings,
  type TriggerConfig,
  type TriggerMeta,
} from "@/lib/notification-api";
import { Loader2 } from "lucide-react";

const TRIGGER_KEYS = [
  "booking_confirmation",
  "pre_arrival",
  "folio_receipt",
] as const;

type TriggerKey = (typeof TRIGGER_KEYS)[number];

function ChannelToggle({
  label,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <Switch checked={checked} disabled={disabled} onCheckedChange={onChange} />
      <span className={disabled ? "text-muted-foreground" : ""}>{label}</span>
    </label>
  );
}

function TriggerEditor({
  meta,
  value,
  providers,
  onChange,
}: {
  meta: TriggerMeta;
  value: TriggerConfig;
  providers: { email: boolean; sms: boolean; whatsapp: boolean };
  onChange: (next: TriggerConfig) => void;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between space-y-0 gap-4">
        <div>
          <CardTitle className="text-base">{meta.label}</CardTitle>
          <CardDescription className="mt-1">{meta.description}</CardDescription>
          <p className="mt-2 text-xs text-muted-foreground">
            Placeholders:{" "}
            {meta.placeholders.map((p) => (
              <code key={p} className="mr-1 rounded bg-muted px-1 py-0.5">
                {`{{${p}}}`}
              </code>
            ))}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Label htmlFor={`en-${meta.key}`} className="text-xs text-muted-foreground">
            Active
          </Label>
          <Switch
            id={`en-${meta.key}`}
            checked={value.enabled}
            onCheckedChange={(enabled) => onChange({ ...value, enabled })}
          />
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap gap-4">
          <ChannelToggle
            label="Email"
            checked={value.channels.email}
            disabled={!providers.email}
            onChange={(email) =>
              onChange({ ...value, channels: { ...value.channels, email } })
            }
          />
          <ChannelToggle
            label="SMS"
            checked={value.channels.sms}
            disabled={!providers.sms}
            onChange={(sms) => onChange({ ...value, channels: { ...value.channels, sms } })}
          />
          <ChannelToggle
            label="WhatsApp"
            checked={value.channels.whatsapp}
            disabled={!providers.whatsapp}
            onChange={(whatsapp) =>
              onChange({ ...value, channels: { ...value.channels, whatsapp } })
            }
          />
        </div>

        <div className="space-y-1.5">
          <Label>Email subject</Label>
          <Input
            value={value.email_subject}
            disabled={!value.enabled}
            onChange={(e) => onChange({ ...value, email_subject: e.target.value })}
          />
        </div>
        <div className="space-y-1.5">
          <Label>Email body</Label>
          <Textarea
            rows={7}
            value={value.email_body}
            disabled={!value.enabled}
            onChange={(e) => onChange({ ...value, email_body: e.target.value })}
            className="font-mono text-xs"
          />
        </div>
        <div className="space-y-1.5">
          <Label>SMS body</Label>
          <Textarea
            rows={3}
            value={value.sms_body}
            disabled={!value.enabled}
            onChange={(e) => onChange({ ...value, sms_body: e.target.value })}
            className="font-mono text-xs"
          />
        </div>
        <div className="space-y-1.5">
          <Label>WhatsApp body</Label>
          <Textarea
            rows={3}
            value={value.whatsapp_body}
            disabled={!value.enabled}
            onChange={(e) => onChange({ ...value, whatsapp_body: e.target.value })}
            className="font-mono text-xs"
          />
        </div>
      </CardContent>
    </Card>
  );
}

export function HotelNotificationsPanel() {
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const settingsQ = useQuery({
    queryKey: ["hotel-notifications"],
    queryFn: getNotificationSettings,
  });
  const logsQ = useQuery({
    queryKey: ["hotel-notification-logs"],
    queryFn: () => getNotificationLogs(20),
  });

  const [draft, setDraft] = useState<NotificationSettings | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (settingsQ.data?.settings) {
      setDraft(settingsQ.data.settings);
    }
  }, [settingsQ.data]);

  async function save() {
    if (!draft) return;
    setSaving(true);
    try {
      const saved = await saveNotificationSettings(draft);
      setDraft(saved);
      await queryClient.invalidateQueries({ queryKey: ["hotel-notifications"] });
      showToast({ type: "success", message: "Notification settings saved" });
    } catch (err) {
      showToast({
        type: "error",
        message: err instanceof Error ? err.message : "Save failed",
      });
    } finally {
      setSaving(false);
    }
  }

  if (settingsQ.isLoading || !draft) {
    return (
      <div className="flex h-40 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const triggers = settingsQ.data?.triggers || [];
  const providers = settingsQ.data?.providers || { email: false, sms: false, whatsapp: false };
  const byKey = Object.fromEntries(triggers.map((t) => [t.key, t])) as Record<
    string,
    TriggerMeta
  >;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Guest messaging</CardTitle>
          <CardDescription>
            Toggle automated emails, SMS, and WhatsApp for booking, pre-arrival, and folio
            receipts. Platform credentials are configured under SuperAdmin → Integrations
            (SendGrid / SMTP / Twilio).
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          <Badge variant={providers.email ? "default" : "secondary"}>
            Email {providers.email ? "ready" : "not configured"}
          </Badge>
          <Badge variant={providers.sms ? "default" : "secondary"}>
            SMS {providers.sms ? "ready" : "not configured"}
          </Badge>
          <Badge variant={providers.whatsapp ? "default" : "secondary"}>
            WhatsApp {providers.whatsapp ? "ready" : "not configured"}
          </Badge>
        </CardContent>
      </Card>

      {TRIGGER_KEYS.map((key) => {
        const meta =
          byKey[key] ||
          ({
            key,
            label: key.replace(/_/g, " "),
            description: "",
            placeholders: [],
            config: draft[key],
          } as TriggerMeta);
        return (
          <TriggerEditor
            key={key}
            meta={meta}
            value={draft[key as TriggerKey]}
            providers={providers}
            onChange={(next) => setDraft((d) => (d ? { ...d, [key]: next } : d))}
          />
        );
      })}

      <div className="flex justify-end">
        <Button onClick={save} disabled={saving}>
          {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
          Save notification settings
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Recent deliveries</CardTitle>
          <CardDescription>Last notification attempts for this property.</CardDescription>
        </CardHeader>
        <CardContent>
          {logsQ.isLoading ? (
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          ) : (logsQ.data || []).length === 0 ? (
            <p className="text-sm text-muted-foreground">No deliveries logged yet.</p>
          ) : (
            <ul className="divide-y text-sm">
              {(logsQ.data || []).map((log) => (
                <li key={String(log.id)} className="flex flex-wrap items-center gap-2 py-2">
                  <Badge variant="outline">{String(log.trigger_key)}</Badge>
                  <Badge variant="secondary">{String(log.channel)}</Badge>
                  <span
                    className={
                      log.status === "sent"
                        ? "text-green-700 dark:text-green-400"
                        : log.status === "failed"
                          ? "text-destructive"
                          : "text-muted-foreground"
                    }
                  >
                    {String(log.status)}
                  </span>
                  <span className="text-muted-foreground">{String(log.recipient || "—")}</span>
                  <span className="ml-auto text-xs text-muted-foreground">
                    {String(log.created_at || "")}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
