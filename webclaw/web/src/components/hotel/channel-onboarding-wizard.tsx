"use client";

import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useToast } from "@/components/toast-provider";
import {
  saveChannelConnection,
  saveChannelMapping,
  syncChannels,
  type ChannelDashboard,
} from "@/lib/channel-api";
import { Check, Loader2 } from "lucide-react";

const STEPS = [
  "Connector",
  "OTAs",
  "Room maps",
  "Rates",
  "Test sync",
] as const;

type Props = {
  data: ChannelDashboard;
  hotelId: string;
  onClose?: () => void;
  onComplete?: () => void;
};

export function ChannelOnboardingWizard({ data, hotelId, onClose, onComplete }: Props) {
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);
  const [provider, setProvider] = useState("channex");
  const [propertyExternalId, setPropertyExternalId] = useState("");
  const [apiBaseUrl, setApiBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [webhookSecret, setWebhookSecret] = useState("");
  const [selectedOtas, setSelectedOtas] = useState<string[]>(["booking_com", "agoda"]);
  const [mapOta, setMapOta] = useState("booking_com");
  const [mapCode, setMapCode] = useState("");
  const [mapRoomTypeId, setMapRoomTypeId] = useState("");
  const [syncResult, setSyncResult] = useState<string | null>(null);

  const roomTypes = useMemo(
    () => (data.room_types || []).filter((rt) => rt.hotel_id === hotelId),
    [data.room_types, hotelId],
  );
  const mappings = useMemo(
    () => (data.mappings || []).filter((m) => m.hotel_id === hotelId),
    [data.mappings, hotelId],
  );
  const connections = useMemo(
    () => (data.connections || []).filter((c) => c.hotel_id === hotelId),
    [data.connections, hotelId],
  );

  function toggleOta(code: string) {
    setSelectedOtas((prev) =>
      prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code],
    );
  }

  async function saveSelectedConnections() {
    if (!hotelId || selectedOtas.length === 0) {
      showToast({ type: "error", message: "Select at least one OTA" });
      return false;
    }
    if (!apiKey && !connections.some((c) => c.api_key_configured)) {
      showToast({ type: "error", message: "API key is required for the first connection" });
      return false;
    }
    setBusy(true);
    try {
      for (const ota of selectedOtas) {
        const body: Record<string, string> = {
          hotel_id: hotelId,
          ota,
          provider,
          property_external_id: propertyExternalId,
          api_base_url: apiBaseUrl,
          status: "connected",
        };
        if (apiKey) body.api_key = apiKey;
        if (webhookSecret) body.webhook_secret = webhookSecret;
        await saveChannelConnection(body);
      }
      await queryClient.invalidateQueries({ queryKey: ["hotel-channels"] });
      showToast({
        type: "success",
        message: `Connected ${selectedOtas.length} OTA${selectedOtas.length === 1 ? "" : "s"}`,
      });
      return true;
    } catch (err) {
      showToast({
        type: "error",
        message: err instanceof Error ? err.message : "Could not save connections",
      });
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function saveMap() {
    if (!mapCode || !mapRoomTypeId) return;
    setBusy(true);
    try {
      await saveChannelMapping({
        hotel_id: hotelId,
        ota: mapOta,
        ota_room_type_code: mapCode,
        room_type_id: mapRoomTypeId,
      });
      setMapCode("");
      await queryClient.invalidateQueries({ queryKey: ["hotel-channels"] });
      showToast({ type: "success", message: "Room mapping saved" });
    } catch (err) {
      showToast({
        type: "error",
        message: err instanceof Error ? err.message : "Mapping failed",
      });
    } finally {
      setBusy(false);
    }
  }

  async function runTestSync() {
    setBusy(true);
    setSyncResult(null);
    try {
      const result = await syncChannels(hotelId);
      const pushed = Array.isArray(result.pushed) ? result.pushed.length : 0;
      const errors = Array.isArray(result.errors) ? result.errors.length : 0;
      const message = errors
        ? `Sync finished with ${errors} error(s); ${pushed} channel(s) updated`
        : `Pushed rates & availability to ${pushed} channel(s)`;
      setSyncResult(message);
      showToast({ type: errors ? "error" : "success", message });
      await queryClient.invalidateQueries({ queryKey: ["hotel-channels"] });
      if (!errors) onComplete?.();
    } catch (err) {
      const message = err instanceof Error ? err.message : "Sync failed";
      setSyncResult(message);
      showToast({ type: "error", message });
    } finally {
      setBusy(false);
    }
  }

  async function next() {
    if (step === 1) {
      const ok = await saveSelectedConnections();
      if (!ok) return;
    }
    if (step === 2 && mappings.length === 0) {
      showToast({
        type: "error",
        message: "Add at least one room type mapping before continuing",
      });
      return;
    }
    setStep((s) => Math.min(s + 1, STEPS.length - 1));
  }

  return (
    <Card className="border-primary/30">
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle>Channel setup wizard</CardTitle>
            <CardDescription>
              Connect OTAs once — rates, availability, and bookings sync two-way after mapping.
            </CardDescription>
          </div>
          {onClose ? (
            <Button variant="ghost" size="sm" onClick={onClose}>
              Close
            </Button>
          ) : null}
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          {STEPS.map((label, idx) => (
            <Badge
              key={label}
              variant={idx === step ? "default" : idx < step ? "secondary" : "outline"}
              className="gap-1"
            >
              {idx < step ? <Check className="h-3 w-3" /> : null}
              {idx + 1}. {label}
            </Badge>
          ))}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {step === 0 ? (
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label>Connector</Label>
              <Select value={provider} onValueChange={setProvider}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(data.providers || ["channex", "siteminder"]).map((p) => (
                    <SelectItem key={p} value={p}>
                      {p === "channex" ? "Channex" : p === "siteminder" ? "SiteMinder" : p}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>External property id</Label>
              <Input
                value={propertyExternalId}
                onChange={(e) => setPropertyExternalId(e.target.value)}
                placeholder="Channel property / hotel id"
              />
            </div>
            <div className="sm:col-span-2">
              <Label>API base URL (optional)</Label>
              <Input
                value={apiBaseUrl}
                onChange={(e) => setApiBaseUrl(e.target.value)}
                placeholder="https://app.channex.io/api/v1"
              />
            </div>
            <div>
              <Label>API key</Label>
              <Input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="Stored per property"
              />
            </div>
            <div>
              <Label>Webhook secret</Label>
              <Input
                type="password"
                value={webhookSecret}
                onChange={(e) => setWebhookSecret(e.target.value)}
                placeholder="HMAC for inbound bookings"
              />
            </div>
            <p className="sm:col-span-2 text-xs text-muted-foreground">
              Inbound bookings POST to <code>{data.webhook_path}</code> with{" "}
              <code>X-OTA-Signature: sha256=…</code>.
            </p>
          </div>
        ) : null}

        {step === 1 ? (
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              Select every OTA this property sells on. Credentials from the previous step are shared.
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
              {(data.otas || []).map((ota) => {
                const checked = selectedOtas.includes(ota.code);
                const existing = connections.find((c) => c.ota === ota.code);
                return (
                  <label
                    key={ota.code}
                    className="flex cursor-pointer items-start gap-3 rounded-md border p-3"
                  >
                    <input
                      type="checkbox"
                      className="mt-1 h-4 w-4"
                      checked={checked}
                      onChange={() => toggleOta(ota.code)}
                    />
                    <div>
                      <div className="font-medium">{ota.label}</div>
                      <div className="text-xs text-muted-foreground">
                        {existing ? `Already ${existing.status}` : "Not connected yet"}
                      </div>
                    </div>
                  </label>
                );
              })}
            </div>
          </div>
        ) : null}

        {step === 2 ? (
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
              Map each OTA room type code to a local room type so bookings allocate a real room.
            </p>
            <div className="grid gap-4 sm:grid-cols-3">
              <div>
                <Label>OTA</Label>
                <Select value={mapOta} onValueChange={setMapOta}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {(data.otas || []).map((ota) => (
                      <SelectItem key={ota.code} value={ota.code}>
                        {ota.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label>OTA room type code</Label>
                <Input
                  value={mapCode}
                  onChange={(e) => setMapCode(e.target.value)}
                  placeholder="e.g. AGODA-DLX"
                />
              </div>
              <div>
                <Label>Local room type</Label>
                <Select value={mapRoomTypeId} onValueChange={setMapRoomTypeId}>
                  <SelectTrigger>
                    <SelectValue placeholder="Select" />
                  </SelectTrigger>
                  <SelectContent>
                    {roomTypes.map((rt) => (
                      <SelectItem key={rt.id} value={rt.id}>
                        {rt.name} ({rt.code})
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <Button
              size="sm"
              disabled={busy || !mapCode || !mapRoomTypeId}
              onClick={saveMap}
            >
              Add mapping
            </Button>
            <div className="divide-y rounded-md border">
              {mappings.length === 0 ? (
                <p className="p-3 text-sm text-muted-foreground">No mappings yet.</p>
              ) : (
                mappings.map((row) => (
                  <div key={row.id} className="p-3 text-sm">
                    <span className="font-medium">{row.ota_label}</span>
                    {" · "}
                    {row.ota_room_type_code} → {row.room_type_name}
                  </div>
                ))
              )}
            </div>
          </div>
        ) : null}

        {step === 3 ? (
          <div className="space-y-2 text-sm">
            <p>
              Nightly rates come from your PMS rate plans and dynamic rate calendar. There is no
              separate OTA rate-plan map — one push updates availability and rates on every
              connected channel.
            </p>
            <p className="text-muted-foreground">
              After go-live, inventory changes at the front desk and yield rules also push
              automatically (with retries on temporary channel errors).
            </p>
          </div>
        ) : null}

        {step === 4 ? (
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              Push rates and availability now to verify the connector. Inbound bookings arrive on
              the webhook once the channel is pointed at this property.
            </p>
            <Button disabled={busy || !hotelId} onClick={runTestSync}>
              {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              Test push now
            </Button>
            {syncResult ? <p className="text-sm">{syncResult}</p> : null}
          </div>
        ) : null}

        <div className="flex flex-wrap justify-between gap-2 pt-2">
          <Button
            variant="outline"
            disabled={busy || step === 0}
            onClick={() => setStep((s) => Math.max(0, s - 1))}
          >
            Back
          </Button>
          {step < STEPS.length - 1 ? (
            <Button disabled={busy} onClick={next}>
              {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              Continue
            </Button>
          ) : (
            <Button variant="secondary" disabled={busy} onClick={onClose}>
              Done
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
