"use client";

import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
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
  deleteChannelMapping,
  getChannelDashboard,
  saveChannelConnection,
  saveChannelMapping,
  syncChannels,
  type ChannelConnection,
} from "@/lib/channel-api";
import { ChannelOnboardingWizard } from "@/components/hotel/channel-onboarding-wizard";
import { Loader2, RotateCcw, Sparkles } from "lucide-react";

function statusVariant(status: string): "default" | "secondary" | "outline" {
  if (status === "connected") return "default";
  if (status === "error") return "secondary";
  return "outline";
}

function formatStamp(value: string | null | undefined) {
  if (!value) return "Never";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

export function ChannelManagerPanel() {
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const dashboardQ = useQuery({
    queryKey: ["hotel-channels"],
    queryFn: getChannelDashboard,
  });
  const data = dashboardQ.data;

  const [hotelId, setHotelId] = useState("");
  const activeHotel = hotelId || data?.hotels[0]?.id || "";
  const roomTypes = useMemo(
    () => (data?.room_types || []).filter((rt) => !activeHotel || rt.hotel_id === activeHotel),
    [data, activeHotel],
  );
  const connections = useMemo(
    () => (data?.connections || []).filter((c) => !activeHotel || c.hotel_id === activeHotel),
    [data, activeHotel],
  );

  const [form, setForm] = useState({
    ota: "booking_com",
    provider: "channex",
    property_external_id: "",
    api_base_url: "",
    api_key: "",
    webhook_secret: "",
    commission_percent: "15",
  });
  const [mapping, setMapping] = useState({
    ota: "booking_com",
    ota_room_type_code: "",
    room_type_id: "",
  });
  const [busy, setBusy] = useState(false);
  const [showWizard, setShowWizard] = useState(false);

  function fillFrom(connection: ChannelConnection) {
    setForm({
      ota: connection.ota,
      provider: connection.provider,
      property_external_id: connection.property_external_id,
      api_base_url: connection.api_base_url,
      api_key: "",
      webhook_secret: "",
      commission_percent: String(connection.commission_percent ?? 15),
    });
  }

  async function onSaveConnection() {
    if (!activeHotel) return;
    setBusy(true);
    try {
      const body: Record<string, string> = {
        hotel_id: activeHotel,
        ota: form.ota,
        provider: form.provider,
        property_external_id: form.property_external_id,
        api_base_url: form.api_base_url,
        status: "connected",
        commission_percent: form.commission_percent || "0",
      };
      if (form.api_key) body.api_key = form.api_key;
      if (form.webhook_secret) body.webhook_secret = form.webhook_secret;
      await saveChannelConnection(body);
      showToast({ type: "success", message: "Channel connection saved" });
      await queryClient.invalidateQueries({ queryKey: ["hotel-channels"] });
    } catch (err) {
      showToast({ type: "error", message: err instanceof Error ? err.message : "Save failed" });
    } finally {
      setBusy(false);
    }
  }

  async function onSaveMapping() {
    if (!activeHotel || !mapping.room_type_id || !mapping.ota_room_type_code) return;
    setBusy(true);
    try {
      await saveChannelMapping({
        hotel_id: activeHotel,
        ota: mapping.ota,
        ota_room_type_code: mapping.ota_room_type_code,
        room_type_id: mapping.room_type_id,
      });
      setMapping((m) => ({ ...m, ota_room_type_code: "" }));
      showToast({ type: "success", message: "Room type mapping saved" });
      await queryClient.invalidateQueries({ queryKey: ["hotel-channels"] });
    } catch (err) {
      showToast({ type: "error", message: err instanceof Error ? err.message : "Mapping failed" });
    } finally {
      setBusy(false);
    }
  }

  async function onSync(ota?: string) {
    setBusy(true);
    try {
      const result = await syncChannels(activeHotel, ota);
      const pushed = Array.isArray(result.pushed) ? result.pushed.length : 0;
      const errors = Array.isArray(result.errors) ? result.errors.length : 0;
      showToast({
        type: errors ? "error" : "success",
        message: errors
          ? `Sync finished with ${errors} error${errors === 1 ? "" : "s"}`
          : `Pushed availability and rates to ${pushed} channel${pushed === 1 ? "" : "s"}`,
      });
      await queryClient.invalidateQueries({ queryKey: ["hotel-channels"] });
    } catch (err) {
      showToast({ type: "error", message: err instanceof Error ? err.message : "Sync failed" });
    } finally {
      setBusy(false);
    }
  }

  if (dashboardQ.isLoading) {
    return (
      <div className="flex h-40 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (dashboardQ.isError) {
    return (
      <p className="text-sm text-muted-foreground">
        {dashboardQ.error instanceof Error ? dashboardQ.error.message : "Could not load channels"}
      </p>
    );
  }

  const errors = data?.errors || [];
  const logs = data?.logs || [];
  const mappings = (data?.mappings || []).filter((m) => !activeHotel || m.hotel_id === activeHotel);

  const needsSetup = connections.length === 0;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-sm text-muted-foreground">
            Webhook{" "}
            <code className="rounded bg-muted px-1 py-0.5">{data?.webhook_path}</code>
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            Sign the raw JSON body with the channel secret and send it as{" "}
            <code>X-OTA-Signature: sha256=…</code>. Supported OTAs: Booking.com, Airbnb, Expedia, Agoda.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          {(data?.hotels.length || 0) > 1 ? (
            <div className="min-w-[180px]">
              <Label>Property</Label>
              <Select value={activeHotel} onValueChange={setHotelId}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {data?.hotels.map((hotel) => (
                    <SelectItem key={hotel.id} value={hotel.id}>
                      {hotel.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ) : null}
          <Button
            variant="outline"
            disabled={!activeHotel || !data}
            onClick={() => setShowWizard(true)}
          >
            <Sparkles className="mr-2 h-4 w-4" />
            {needsSetup ? "Start setup wizard" : "Setup wizard"}
          </Button>
          <Button disabled={busy || !activeHotel} onClick={() => onSync()}>
            {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
            Push rates & availability
          </Button>
        </div>
      </div>

      {showWizard && data && activeHotel ? (
        <ChannelOnboardingWizard
          data={data}
          hotelId={activeHotel}
          onClose={() => setShowWizard(false)}
          onComplete={() => setShowWizard(false)}
        />
      ) : null}

      {!showWizard && needsSetup && data && activeHotel ? (
        <Card className="border-dashed">
          <CardHeader>
            <CardTitle className="text-base">New property? Use the setup wizard</CardTitle>
            <CardDescription>
              Connect Channex or SiteMinder, enable Booking.com / Airbnb / Expedia / Agoda, map
              room types, then test a two-way sync — without editing each OTA extranet.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button onClick={() => setShowWizard(true)}>
              <Sparkles className="mr-2 h-4 w-4" />
              Start setup wizard
            </Button>
          </CardContent>
        </Card>
      ) : null}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {(data?.otas || []).map((ota) => {
          const connection = connections.find((c) => c.ota === ota.code);
          return (
            <Card key={ota.code}>
              <CardHeader className="pb-2">
                <div className="flex items-center justify-between gap-2">
                  <CardTitle className="text-base">{ota.label}</CardTitle>
                  <Badge variant={statusVariant(connection?.status || "disconnected")}>
                    {connection?.status || "disconnected"}
                  </Badge>
                </div>
                <CardDescription>
                  {connection
                    ? `${connection.provider} · ${connection.property_external_id || "no property id"}`
                    : "Not connected"}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-1 text-sm">
                <div>Last success: {formatStamp(connection?.last_success_at)}</div>
                {connection?.last_error ? (
                  <div className="text-destructive">{connection.last_error}</div>
                ) : null}
                <div className="flex flex-wrap gap-2 pt-2">
                  {connection ? (
                    <Button variant="outline" size="sm" onClick={() => fillFrom(connection)}>
                      Edit
                    </Button>
                  ) : null}
                  {connection && connection.status !== "disconnected" ? (
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={busy}
                      onClick={() => onSync(ota.code)}
                    >
                      <RotateCcw className="mr-1 h-3.5 w-3.5" />
                      Retry sync
                    </Button>
                  ) : null}
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Connect a channel</CardTitle>
          <CardDescription>
            Channex uses <code>user-api-key</code>. SiteMinder uses a Bearer token against your
            gateway URL. Leave secrets blank to keep the saved values.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label>OTA</Label>
            <Select value={form.ota} onValueChange={(ota) => setForm((f) => ({ ...f, ota }))}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {(data?.otas || []).map((ota) => (
                  <SelectItem key={ota.code} value={ota.code}>{ota.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label>Connector</Label>
            <Select value={form.provider} onValueChange={(provider) => setForm((f) => ({ ...f, provider }))}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="channex">Channex</SelectItem>
                <SelectItem value="siteminder">SiteMinder</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label>External property id</Label>
            <Input
              value={form.property_external_id}
              onChange={(e) => setForm((f) => ({ ...f, property_external_id: e.target.value }))}
              placeholder="Channex / SiteMinder property id"
            />
          </div>
          <div>
            <Label>API base URL</Label>
            <Input
              value={form.api_base_url}
              onChange={(e) => setForm((f) => ({ ...f, api_base_url: e.target.value }))}
              placeholder="https://app.channex.io/api/v1"
            />
          </div>
          <div>
            <Label>API key</Label>
            <Input
              type="password"
              value={form.api_key}
              onChange={(e) => setForm((f) => ({ ...f, api_key: e.target.value }))}
              placeholder="Stored on this property"
            />
          </div>
          <div>
            <Label>Webhook secret</Label>
            <Input
              type="password"
              value={form.webhook_secret}
              onChange={(e) => setForm((f) => ({ ...f, webhook_secret: e.target.value }))}
              placeholder="HMAC secret for inbound bookings"
            />
          </div>
          <div>
            <Label>Commission % (estimate)</Label>
            <Input
              type="number"
              min={0}
              max={100}
              step="0.1"
              value={form.commission_percent}
              onChange={(e) => setForm((f) => ({ ...f, commission_percent: e.target.value }))}
            />
          </div>
          <div className="sm:col-span-2 flex flex-wrap gap-2">
            <Button disabled={busy || !activeHotel} onClick={onSaveConnection}>
              Save connection
            </Button>
            <Button variant="outline" asChild>
              <a href="/app/hotel/inventory/allotments">Channel allotments</a>
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Room type mappings</CardTitle>
          <CardDescription>
            Map each OTA room type code to a local room type. Inbound bookings allocate a free
            room number at this property.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-3">
            <div>
              <Label>OTA</Label>
              <Select value={mapping.ota} onValueChange={(ota) => setMapping((m) => ({ ...m, ota }))}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {(data?.otas || []).map((ota) => (
                    <SelectItem key={ota.code} value={ota.code}>{ota.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>OTA room type code</Label>
              <Input
                value={mapping.ota_room_type_code}
                onChange={(e) => setMapping((m) => ({ ...m, ota_room_type_code: e.target.value }))}
                placeholder="BDC-DLX"
              />
            </div>
            <div>
              <Label>Local room type</Label>
              <Select
                value={mapping.room_type_id}
                onValueChange={(room_type_id) => setMapping((m) => ({ ...m, room_type_id }))}
              >
                <SelectTrigger><SelectValue placeholder="Select room type" /></SelectTrigger>
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
          <Button disabled={busy || !mapping.room_type_id || !mapping.ota_room_type_code} onClick={onSaveMapping}>
            Save mapping
          </Button>
          <div className="divide-y rounded-md border">
            {mappings.length === 0 ? (
              <p className="p-3 text-sm text-muted-foreground">No mappings yet.</p>
            ) : mappings.map((row) => (
              <div key={row.id} className="flex flex-wrap items-center justify-between gap-2 p-3 text-sm">
                <div>
                  <div className="font-medium">
                    {row.ota_label} · {row.ota_room_type_code} → {row.room_type_name}
                  </div>
                  <div className="text-muted-foreground">
                    Rooms {row.room_numbers.length ? row.room_numbers.join(", ") : "none"}
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={async () => {
                    await deleteChannelMapping(row.id);
                    await queryClient.invalidateQueries({ queryKey: ["hotel-channels"] });
                  }}
                >
                  Remove
                </Button>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Failed channel mappings</CardTitle>
          <CardDescription>Errors from the latest sync and inbound bookings.</CardDescription>
        </CardHeader>
        <CardContent>
          {errors.length === 0 ? (
            <p className="text-sm text-muted-foreground">No channel errors.</p>
          ) : (
            <ul className="space-y-2 text-sm">
              {errors.map((row) => (
                <li key={row.id} className="rounded-md border border-destructive/30 p-3">
                  <div className="font-medium">{row.ota_label || row.kind} · {row.kind}</div>
                  <div>{row.message}</div>
                  <div className="text-xs text-muted-foreground">{formatStamp(row.created_at)}</div>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Sync log</CardTitle>
        </CardHeader>
        <CardContent>
          {logs.length === 0 ? (
            <p className="text-sm text-muted-foreground">No sync activity yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-muted-foreground">
                    <th className="py-2 pr-3 font-medium">When</th>
                    <th className="py-2 pr-3 font-medium">Channel</th>
                    <th className="py-2 pr-3 font-medium">Direction</th>
                    <th className="py-2 pr-3 font-medium">Status</th>
                    <th className="py-2 font-medium">Message</th>
                  </tr>
                </thead>
                <tbody>
                  {logs.map((row) => (
                    <tr key={row.id} className="border-b last:border-0">
                      <td className="py-2 pr-3 whitespace-nowrap">{formatStamp(row.created_at)}</td>
                      <td className="py-2 pr-3">{row.ota_label || "—"}</td>
                      <td className="py-2 pr-3">{row.direction}</td>
                      <td className="py-2 pr-3">
                        <Badge variant={row.status === "success" ? "default" : "secondary"}>{row.status}</Badge>
                      </td>
                      <td className="py-2">{row.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
