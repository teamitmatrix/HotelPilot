"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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
import { fetchAvailabilityGrid, type AvailabilityCell } from "@/lib/inventory-api";
import { ArrowLeft, ChevronLeft, ChevronRight, Loader2 } from "lucide-react";

function pad(n: number) {
  return String(n).padStart(2, "0");
}

function monthBounds(year: number, monthIndex: number) {
  const start = `${year}-${pad(monthIndex + 1)}-01`;
  const last = new Date(year, monthIndex + 1, 0).getDate();
  const end = `${year}-${pad(monthIndex + 1)}-${pad(last)}`;
  return { start, end };
}

export default function AvailabilityGridPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const canView = perms.canManageRooms || perms.canGenerateReports;

  const hotelsQ = useEntityList(SKILL, "list-hotels", { limit: "20" }, { enabled: canView });
  const hotels = itemsFrom<Hotel>(hotelsQ.data, "hotels");
  const hotelId = hotels[0]?.id ?? "";

  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [monthIndex, setMonthIndex] = useState(now.getMonth());
  const { start, end } = monthBounds(year, monthIndex);
  const monthLabel = new Date(year, monthIndex, 1).toLocaleString(undefined, {
    month: "long",
    year: "numeric",
  });

  const gridQ = useQuery({
    queryKey: ["availability-grid", hotelId, start, end],
    queryFn: () => fetchAvailabilityGrid({ hotel_id: hotelId, start_date: start, end_date: end }),
    enabled: canView && !!hotelId,
  });

  const cellMap = useMemo(() => {
    const map = new Map<string, AvailabilityCell>();
    for (const cell of gridQ.data?.cells || []) {
      map.set(`${cell.room_type_id}|${cell.date}`, cell);
    }
    return map;
  }, [gridQ.data]);

  function shiftMonth(delta: number) {
    const d = new Date(year, monthIndex + delta, 1);
    setYear(d.getFullYear());
    setMonthIndex(d.getMonth());
  }

  return (
    <div className="flex flex-col">
      <header className="flex h-14 items-center gap-4 border-b bg-background px-6">
        <SidebarTrigger />
        <Separator orientation="vertical" className="h-6" />
        <h1 className="text-lg font-semibold">Availability grid</h1>
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
          <div className="flex gap-2">
            <Button variant="outline" size="sm" asChild>
              <Link href="/app/hotel/inventory/restrictions">Restrictions</Link>
            </Button>
            <Button variant="outline" size="sm" asChild>
              <Link href="/app/hotel/inventory/dynamic-rates">Rates</Link>
            </Button>
          </div>
        </div>

        <HotelOpsHeader
          title="Availability grid"
          subtitle="Sellable rooms by type and night (physical − unassigned holds − stop-sell)"
          perms={perms}
          actions={
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" onClick={() => shiftMonth(-1)}>
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <span className="min-w-[140px] text-center text-sm font-medium">{monthLabel}</span>
              <Button variant="outline" size="sm" onClick={() => shiftMonth(1)}>
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          }
        />

        {!canView ? (
          <p className="text-sm text-muted-foreground">Inventory access required.</p>
        ) : hotelsQ.isLoading || gridQ.isLoading ? (
          <div className="flex h-48 items-center justify-center">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : gridQ.isError ? (
          <Card>
            <CardContent className="p-6 text-sm text-destructive">
              {gridQ.error instanceof Error ? gridQ.error.message : "Could not load availability"}
            </CardContent>
          </Card>
        ) : (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">{gridQ.data?.hotel_name || "Property"}</CardTitle>
              <CardDescription>
                {gridQ.data?.accepting_bookings
                  ? "Branch is open for bookings"
                  : "Branch closed — sellable inventory shows as zero"}
              </CardDescription>
            </CardHeader>
            <CardContent className="overflow-x-auto">
              <table className="w-max min-w-full border-collapse text-xs">
                <thead>
                  <tr>
                    <th className="sticky left-0 z-10 bg-background px-2 py-1 text-left font-medium">
                      Room type
                    </th>
                    {(gridQ.data?.dates || []).map((d) => (
                      <th key={d} className="px-1 py-1 text-center font-medium text-muted-foreground">
                        {d.slice(8)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(gridQ.data?.room_types || []).map((rt) => (
                    <tr key={rt.id} className="border-t">
                      <td className="sticky left-0 z-10 bg-background px-2 py-1 font-medium whitespace-nowrap">
                        {rt.name}
                        <span className="ml-1 text-muted-foreground">({rt.physical_rooms})</span>
                      </td>
                      {(gridQ.data?.dates || []).map((d) => {
                        const cell = cellMap.get(`${rt.id}|${d}`);
                        const sellable = cell?.sellable ?? 0;
                        const stopped = cell?.stop_sell;
                        const tone =
                          stopped || sellable === 0
                            ? "bg-destructive/15 text-destructive"
                            : sellable <= 1
                              ? "bg-amber-500/15 text-amber-900 dark:text-amber-100"
                              : "bg-emerald-500/10";
                        return (
                          <td
                            key={d}
                            title={
                              cell
                                ? `Sellable ${cell.sellable} · physical ${cell.physical} · assigned ${cell.assigned} · unassigned ${cell.unassigned}${stopped ? " · STOP SELL" : ""}`
                                : undefined
                            }
                            className={`px-1 py-1 text-center tabular-nums ${tone}`}
                          >
                            {stopped ? "X" : sellable}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
              {(gridQ.data?.room_types || []).length === 0 ? (
                <p className="py-8 text-center text-sm text-muted-foreground">
                  No room types configured.
                </p>
              ) : null}
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
