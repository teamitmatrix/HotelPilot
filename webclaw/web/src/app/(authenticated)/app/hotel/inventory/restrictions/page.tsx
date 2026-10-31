"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
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
import { SidebarTrigger } from "@/components/ui/sidebar";
import { Separator } from "@/components/ui/separator";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";
import { HotelOpsHeader } from "@/components/hotel/hotel-ops-header";
import { useAuth } from "@/lib/auth";
import { useHotelPermissions } from "@/lib/rbac";
import { useEntityList } from "@/lib/hooks";
import { useToast } from "@/components/toast-provider";
import { SKILL, type Hotel, type RoomType, itemsFrom } from "@/lib/hospitality";
import {
  clearInventoryRestrictions,
  fetchInventoryRestrictions,
  saveInventoryRestrictions,
} from "@/lib/inventory-api";
import { ArrowLeft, Loader2 } from "lucide-react";

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

function plusDays(iso: string, days: number) {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

export default function InventoryRestrictionsPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const canEdit = perms.canManageRooms;

  const hotelsQ = useEntityList(SKILL, "list-hotels", { limit: "20" }, { enabled: canEdit });
  const typesQ = useEntityList(SKILL, "list-room-types", { limit: "200" }, { enabled: canEdit });
  const hotels = itemsFrom<Hotel>(hotelsQ.data, "hotels");
  const types = itemsFrom<RoomType>(typesQ.data, "room_types");
  const hotelId = hotels[0]?.id ?? "";

  const [roomTypeId, setRoomTypeId] = useState<string>("");
  const [ota, setOta] = useState<string>("");
  const [ratePlanId, setRatePlanId] = useState<string>("");
  const [startDate, setStartDate] = useState(todayIso);
  const [endDate, setEndDate] = useState(() => plusDays(todayIso(), 6));
  const [stopSell, setStopSell] = useState(false);
  const [cta, setCta] = useState(false);
  const [ctd, setCtd] = useState(false);
  const [minNights, setMinNights] = useState("1");
  const [maxNights, setMaxNights] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);

  const ratesQ = useEntityList(SKILL, "list-rate-plans", { limit: "200" }, { enabled: canEdit });
  const ratePlans = itemsFrom<{ id: string; name: string; code: string }>(ratesQ.data, "rate_plans");

  const listStart = startDate;
  const listEnd = endDate >= startDate ? endDate : startDate;

  const controlsQ = useQuery({
    queryKey: ["inventory-restrictions", hotelId, listStart, listEnd, roomTypeId],
    queryFn: () =>
      fetchInventoryRestrictions({
        hotel_id: hotelId,
        start_date: listStart,
        end_date: listEnd,
        room_type_id: roomTypeId || undefined,
      }),
    enabled: canEdit && !!hotelId,
  });

  const activeTypeLabel = useMemo(() => {
    if (!roomTypeId) return "All room types";
    return types.find((t) => t.id === roomTypeId)?.name || roomTypeId;
  }, [roomTypeId, types]);

  async function onSave() {
    if (!hotelId) return;
    setBusy(true);
    try {
      await saveInventoryRestrictions({
        hotel_id: hotelId,
        room_type_id: roomTypeId || null,
        rate_plan_id: ratePlanId || null,
        ota: ota || null,
        start_date: startDate,
        end_date: endDate,
        stop_sell: stopSell,
        closed_to_arrival: cta,
        closed_to_departure: ctd,
        min_nights: Number(minNights) || 1,
        max_nights: maxNights ? Number(maxNights) : null,
        notes,
      });
      showToast({ type: "success", message: "Restrictions saved" });
      await queryClient.invalidateQueries({ queryKey: ["inventory-restrictions"] });
      await queryClient.invalidateQueries({ queryKey: ["restriction-calendar"] });
      await queryClient.invalidateQueries({ queryKey: ["availability-grid"] });
    } catch (err) {
      showToast({
        type: "error",
        message: err instanceof Error ? err.message : "Could not save restrictions",
      });
    } finally {
      setBusy(false);
    }
  }

  async function onClear() {
    if (!hotelId) return;
    setBusy(true);
    try {
      const result = await clearInventoryRestrictions({
        hotel_id: hotelId,
        room_type_id: roomTypeId || null,
        start_date: startDate,
        end_date: endDate,
      });
      showToast({
        type: "success",
        message: `Cleared ${Number(result.deleted || 0)} restriction row(s)`,
      });
      await queryClient.invalidateQueries({ queryKey: ["inventory-restrictions"] });
      await queryClient.invalidateQueries({ queryKey: ["availability-grid"] });
    } catch (err) {
      showToast({
        type: "error",
        message: err instanceof Error ? err.message : "Could not clear restrictions",
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col">
      <header className="flex h-14 items-center gap-4 border-b bg-background px-6">
        <SidebarTrigger />
        <Separator orientation="vertical" className="h-6" />
        <h1 className="text-lg font-semibold">Inventory restrictions</h1>
        <div className="ml-auto flex items-center gap-2">
          <ConnectionStatus />
          <ThemeToggle />
          <ChatToggleButton />
        </div>
      </header>
      <div className="flex-1 space-y-6 p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Button variant="ghost" size="sm" className="-ml-2" asChild>
            <Link href="/app/hotel/inventory">
              <ArrowLeft className="mr-1 h-4 w-4" />
              Inventory
            </Link>
          </Button>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" asChild>
              <Link href="/app/hotel/inventory/restriction-calendar">Restriction calendar</Link>
            </Button>
            <Button variant="outline" size="sm" asChild>
              <Link href="/app/hotel/inventory/availability">Availability grid</Link>
            </Button>
            <Button variant="outline" size="sm" asChild>
              <Link href="/app/hotel/inventory/allotments">Channel allotments</Link>
            </Button>
          </div>
        </div>

        <HotelOpsHeader
          title="Restrictions"
          subtitle="Stop-sell, CTA/CTD, and LOS by room type, rate plan, and channel"
          perms={perms}
        />

        {!canEdit ? (
          <p className="text-sm text-muted-foreground">Room management access required.</p>
        ) : (
          <div className="grid gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Apply to date range</CardTitle>
                <CardDescription>
                  Applies to <strong>{activeTypeLabel}</strong> for each night in the range.
                  Enforced on front-desk and OTA bookings.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div>
                  <Label>Room type</Label>
                  <Select
                    value={roomTypeId || "__all__"}
                    onValueChange={(v) => setRoomTypeId(v === "__all__" ? "" : v)}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__all__">All room types</SelectItem>
                      {types.map((t) => (
                        <SelectItem key={t.id} value={t.id}>
                          {t.name} ({t.code})
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <Label>Rate plan (optional)</Label>
                    <Select
                      value={ratePlanId || "__all__"}
                      onValueChange={(v) => setRatePlanId(v === "__all__" ? "" : v)}
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="__all__">All rate plans</SelectItem>
                        {ratePlans.map((r) => (
                          <SelectItem key={r.id} value={r.id}>
                            {r.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div>
                    <Label>Channel / OTA (optional)</Label>
                    <Select
                      value={ota || "__all__"}
                      onValueChange={(v) => setOta(v === "__all__" ? "" : v)}
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="__all__">All channels</SelectItem>
                        <SelectItem value="booking_com">Booking.com</SelectItem>
                        <SelectItem value="expedia">Expedia</SelectItem>
                        <SelectItem value="agoda">Agoda</SelectItem>
                        <SelectItem value="airbnb">Airbnb</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <Label>From</Label>
                    <Input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
                  </div>
                  <div>
                    <Label>To</Label>
                    <Input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
                  </div>
                </div>
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={stopSell} onChange={(e) => setStopSell(e.target.checked)} />
                  Stop-sell (zero availability)
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={cta} onChange={(e) => setCta(e.target.checked)} />
                  Closed to arrival (CTA)
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={ctd} onChange={(e) => setCtd(e.target.checked)} />
                  Closed to departure (CTD)
                </label>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <Label>Min nights</Label>
                    <Input
                      type="number"
                      min={1}
                      value={minNights}
                      onChange={(e) => setMinNights(e.target.value)}
                    />
                  </div>
                  <div>
                    <Label>Max nights (optional)</Label>
                    <Input
                      type="number"
                      min={1}
                      value={maxNights}
                      onChange={(e) => setMaxNights(e.target.value)}
                      placeholder="No max"
                    />
                  </div>
                </div>
                <div>
                  <Label>Notes</Label>
                  <Input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Optional" />
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button disabled={busy || !hotelId} onClick={onSave}>
                    {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                    Save restrictions
                  </Button>
                  <Button variant="outline" disabled={busy || !hotelId} onClick={onClear}>
                    Clear range
                  </Button>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-base">Active in selected range</CardTitle>
                <CardDescription>
                  {listStart} → {listEnd}
                </CardDescription>
              </CardHeader>
              <CardContent>
                {controlsQ.isLoading ? (
                  <div className="flex h-32 items-center justify-center">
                    <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                  </div>
                ) : (controlsQ.data || []).length === 0 ? (
                  <p className="text-sm text-muted-foreground">No manual restrictions in this range.</p>
                ) : (
                  <ul className="max-h-[420px] space-y-2 overflow-y-auto text-sm">
                    {(controlsQ.data || []).map((row) => (
                      <li key={row.id} className="rounded-md border p-3">
                        <div className="font-medium">
                          {row.stay_date}
                          <span className="ml-2 text-muted-foreground">
                            {row.room_type_id
                              ? types.find((t) => t.id === row.room_type_id)?.name || "Room type"
                              : "All types"}
                          </span>
                        </div>
                        <div className="mt-1 text-xs text-muted-foreground">
                          {[
                            row.stop_sell ? "stop-sell" : null,
                            row.closed_to_arrival ? "CTA" : null,
                            row.closed_to_departure ? "CTD" : null,
                            `min ${row.min_nights}`,
                            row.max_nights != null ? `max ${row.max_nights}` : null,
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}
