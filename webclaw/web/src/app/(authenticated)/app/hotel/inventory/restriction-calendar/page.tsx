"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
import { SKILL, type Hotel, itemsFrom } from "@/lib/hospitality";
import {
  fetchRestrictionCalendar,
  type RestrictionCalendarCell,
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

export default function RestrictionCalendarPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const canView = perms.canManageRooms || perms.canManageHotel;
  const hotelsQ = useEntityList(SKILL, "list-hotels", { limit: "20" }, { enabled: canView });
  const hotels = itemsFrom<Hotel>(hotelsQ.data, "hotels");
  const hotelId = hotels[0]?.id ?? "";

  const [startDate, setStartDate] = useState(todayIso);
  const [endDate, setEndDate] = useState(() => plusDays(todayIso(), 13));
  const [ota, setOta] = useState("");
  const [ratePlanId, setRatePlanId] = useState("");

  const calQ = useQuery({
    queryKey: ["restriction-calendar", hotelId, startDate, endDate, ota, ratePlanId],
    queryFn: () =>
      fetchRestrictionCalendar({
        hotel_id: hotelId,
        start_date: startDate,
        end_date: endDate >= startDate ? endDate : startDate,
        ota: ota || undefined,
        rate_plan_id: ratePlanId || undefined,
      }),
    enabled: canView && !!hotelId,
  });

  const cellIndex = useMemo(() => {
    const map = new Map<string, RestrictionCalendarCell>();
    for (const c of calQ.data?.cells || []) {
      map.set(`${c.room_type_id}:${c.stay_date}`, c);
    }
    return map;
  }, [calQ.data]);

  return (
    <div className="flex flex-col">
      <header className="flex h-14 items-center gap-4 border-b bg-background px-6">
        <SidebarTrigger />
        <Separator orientation="vertical" className="h-6" />
        <h1 className="text-lg font-semibold">Restriction calendar</h1>
        <div className="ml-auto flex items-center gap-2">
          <ConnectionStatus />
          <ThemeToggle />
          <ChatToggleButton />
        </div>
      </header>
      <div className="flex-1 space-y-6 p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Button variant="ghost" size="sm" className="-ml-2" asChild>
            <Link href="/app/hotel/inventory/restrictions">
              <ArrowLeft className="mr-1 h-4 w-4" />
              Edit restrictions
            </Link>
          </Button>
        </div>
        <HotelOpsHeader
          title="Restriction calendar"
          subtitle="Room type × night view of stop-sell, CTA, CTD, and LOS (filter by rate / channel)"
          perms={perms}
        />
        {!canView ? (
          <p className="text-sm text-muted-foreground">Room management access required.</p>
        ) : (
          <>
            <div className="flex flex-wrap items-end gap-3">
              <div>
                <Label className="text-xs text-muted-foreground">From</Label>
                <Input type="date" className="w-[150px]" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
              </div>
              <div>
                <Label className="text-xs text-muted-foreground">To</Label>
                <Input type="date" className="w-[150px]" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
              </div>
              <div>
                <Label className="text-xs text-muted-foreground">Channel</Label>
                <Select value={ota || "__all__"} onValueChange={(v) => setOta(v === "__all__" ? "" : v)}>
                  <SelectTrigger className="w-[150px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__all__">All</SelectItem>
                    <SelectItem value="booking_com">Booking.com</SelectItem>
                    <SelectItem value="expedia">Expedia</SelectItem>
                    <SelectItem value="agoda">Agoda</SelectItem>
                    <SelectItem value="airbnb">Airbnb</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {(calQ.data?.rate_plans || []).length > 0 ? (
                <div>
                  <Label className="text-xs text-muted-foreground">Rate plan</Label>
                  <Select
                    value={ratePlanId || "__all__"}
                    onValueChange={(v) => setRatePlanId(v === "__all__" ? "" : v)}
                  >
                    <SelectTrigger className="w-[160px]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__all__">All</SelectItem>
                      {(calQ.data?.rate_plans || []).map((r) => (
                        <SelectItem key={r.id} value={r.id}>
                          {r.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              ) : null}
            </div>

            {calQ.isLoading ? (
              <div className="flex h-40 items-center justify-center">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            ) : calQ.isError ? (
              <p className="text-sm text-destructive">Could not load calendar.</p>
            ) : (
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">
                    {calQ.data?.hotel_name} · {calQ.data?.start_date} → {calQ.data?.end_date}
                  </CardTitle>
                </CardHeader>
                <CardContent className="overflow-x-auto">
                  <table className="w-full min-w-[640px] border-collapse text-xs">
                    <thead>
                      <tr>
                        <th className="sticky left-0 bg-background p-2 text-left">Room type</th>
                        {(calQ.data?.dates || []).map((d) => (
                          <th key={d} className="p-1 text-center font-normal text-muted-foreground">
                            {d.slice(5)}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {(calQ.data?.room_types || []).map((rt) => (
                        <tr key={rt.id} className="border-t">
                          <td className="sticky left-0 bg-background p-2 font-medium">{rt.name}</td>
                          {(calQ.data?.dates || []).map((d) => {
                            const cell = cellIndex.get(`${rt.id}:${d}`);
                            const flags: string[] = [];
                            if (cell?.stop_sell) flags.push("SS");
                            if (cell?.closed_to_arrival) flags.push("CTA");
                            if (cell?.closed_to_departure) flags.push("CTD");
                            if (cell && cell.min_nights > 1) flags.push(`min${cell.min_nights}`);
                            if (cell?.max_nights) flags.push(`max${cell.max_nights}`);
                            return (
                              <td key={d} className="p-1 text-center align-middle">
                                {flags.length ? (
                                  <Badge
                                    variant={cell?.stop_sell ? "destructive" : "secondary"}
                                    className="px-1 py-0 text-[10px]"
                                  >
                                    {flags.join("·")}
                                  </Badge>
                                ) : (
                                  <span className="text-muted-foreground">·</span>
                                )}
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="mt-3 text-xs text-muted-foreground">
                    SS = stop-sell · CTA / CTD = closed to arrival / departure. Edit via Restrictions.
                  </p>
                </CardContent>
              </Card>
            )}
          </>
        )}
      </div>
    </div>
  );
}
