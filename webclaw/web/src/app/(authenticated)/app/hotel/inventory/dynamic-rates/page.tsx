"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
import { useEntityList, useEntityMutation } from "@/lib/hooks";
import { useToast } from "@/components/toast-provider";
import {
  SKILL,
  type Hotel,
  type RatePlan,
  type RoomType,
  formatMoney,
  hotelCurrency,
  itemsFrom,
} from "@/lib/hospitality";
import {
  fetchRateCalendar,
  setRateWindow,
  type NightlyRate,
} from "@/lib/rate-calendar-api";
import { ArrowLeft, ChevronLeft, ChevronRight, Loader2, Trash2 } from "lucide-react";

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function pad(n: number) {
  return String(n).padStart(2, "0");
}

function monthBounds(year: number, monthIndex: number) {
  const start = `${year}-${pad(monthIndex + 1)}-01`;
  const last = new Date(year, monthIndex + 1, 0).getDate();
  const end = `${year}-${pad(monthIndex + 1)}-${pad(last)}`;
  return { start, end, last };
}

function activeFlag(value: unknown): boolean {
  return value === 1 || value === true || value === "1";
}

export default function DynamicRatesPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const mutate = useEntityMutation(SKILL);
  const canEdit = perms.canManageRooms;

  const hotelsQ = useEntityList(SKILL, "list-hotels", { limit: "20" }, { enabled: perms.canManageRooms });
  const typesQ = useEntityList(SKILL, "list-room-types", { limit: "200" }, { enabled: perms.canManageRooms });
  const ratesQ = useEntityList(SKILL, "list-rate-plans", { limit: "500" }, { enabled: perms.canManageRooms });

  const hotels = itemsFrom<Hotel>(hotelsQ.data, "hotels");
  const types = itemsFrom<RoomType>(typesQ.data, "room_types");
  const ratePlans = itemsFrom<RatePlan>(ratesQ.data, "rate_plans");

  const hotelId = hotels[0]?.id ?? "";
  const currency = hotelCurrency(hotels[0]?.currency);

  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [monthIndex, setMonthIndex] = useState(now.getMonth());
  const [roomTypeId, setRoomTypeId] = useState("");
  const activeTypeId = roomTypeId || types[0]?.id || "";

  const { start, end, last } = monthBounds(year, monthIndex);
  const monthLabel = new Date(year, monthIndex, 1).toLocaleString(undefined, {
    month: "long",
    year: "numeric",
  });

  const calQ = useQuery({
    queryKey: ["rate-calendar", hotelId, activeTypeId, start, end],
    queryFn: () =>
      fetchRateCalendar({
        hotel_id: hotelId,
        room_type_id: activeTypeId,
        start_date: start,
        end_date: end,
      }),
    enabled: Boolean(hotelId && activeTypeId && perms.canManageRooms),
  });

  const byDate = useMemo(() => {
    const map = new Map<string, NightlyRate>();
    for (const night of calQ.data?.nightly_rates || []) {
      map.set(night.date, night);
    }
    return map;
  }, [calQ.data]);

  const monthWindows = useMemo(() => {
    return ratePlans
      .filter((p) => {
        if (!activeFlag(p.is_active)) return false;
        if (p.hotel_id && hotelId && p.hotel_id !== hotelId) return false;
        // Room-type windows for this type, plus hotel-wide (null) windows
        if (p.room_type_id && p.room_type_id !== activeTypeId) return false;
        return p.start_date <= end && p.end_date >= start;
      })
      .sort((a, b) => a.start_date.localeCompare(b.start_date) || (b.priority ?? 0) - (a.priority ?? 0));
  }, [ratePlans, hotelId, activeTypeId, start, end]);

  const [monthOpen, setMonthOpen] = useState(false);
  const [rangeOpen, setRangeOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [monthRate, setMonthRate] = useState("");
  const [rangeForm, setRangeForm] = useState({
    start_date: start,
    end_date: start,
    nightly_rate: "",
    name: "",
  });

  const firstWeekday = new Date(year, monthIndex, 1).getDay();
  const cells: Array<{ day: number | null; iso?: string; night?: NightlyRate }> = [];
  for (let i = 0; i < firstWeekday; i++) cells.push({ day: null });
  for (let d = 1; d <= last; d++) {
    const iso = `${year}-${pad(monthIndex + 1)}-${pad(d)}`;
    cells.push({ day: d, iso, night: byDate.get(iso) });
  }

  function shiftMonth(delta: number) {
    const next = new Date(year, monthIndex + delta, 1);
    setYear(next.getFullYear());
    setMonthIndex(next.getMonth());
  }

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["rate-calendar"] }),
      queryClient.invalidateQueries({ queryKey: ["entity-list", SKILL, "list-rate-plans"] }),
    ]);
  }

  async function saveMonthRate() {
    if (!hotelId || !activeTypeId || !monthRate) return;
    setBusy(true);
    try {
      await setRateWindow({
        hotel_id: hotelId,
        room_type_id: activeTypeId,
        start_date: start,
        end_date: end,
        nightly_rate: monthRate,
        name: `${types.find((t) => t.id === activeTypeId)?.name || "Room"} ${monthLabel}`,
      });
      showToast({ type: "success", message: `Saved ${monthLabel} rate` });
      setMonthOpen(false);
      await refresh();
    } catch (err) {
      showToast({ type: "error", message: err instanceof Error ? err.message : "Save failed" });
    } finally {
      setBusy(false);
    }
  }

  async function saveRangeRate() {
    if (!hotelId || !activeTypeId || !rangeForm.nightly_rate) return;
    if (rangeForm.end_date < rangeForm.start_date) {
      showToast({ type: "error", message: "End date must be on or after start date" });
      return;
    }
    setBusy(true);
    try {
      await setRateWindow({
        hotel_id: hotelId,
        room_type_id: activeTypeId,
        start_date: rangeForm.start_date,
        end_date: rangeForm.end_date,
        nightly_rate: rangeForm.nightly_rate,
        name: rangeForm.name || undefined,
      });
      showToast({ type: "success", message: "Date-range rate saved" });
      setRangeOpen(false);
      await refresh();
    } catch (err) {
      showToast({ type: "error", message: err instanceof Error ? err.message : "Save failed" });
    } finally {
      setBusy(false);
    }
  }

  async function removeWindow(id: string, label: string) {
    if (!confirm(`Delete rate window “${label}”?`)) return;
    try {
      await mutate.mutateAsync({ action: "delete-rate-plan", body: { id } });
      showToast({ type: "success", message: "Rate window deleted" });
      await refresh();
    } catch (err) {
      showToast({ type: "error", message: err instanceof Error ? err.message : "Delete failed" });
    }
  }

  if (!perms.canManageRooms) {
    return (
      <div className="p-6 text-sm text-muted-foreground">
        You do not have permission to manage dynamic rates.
      </div>
    );
  }

  const baseRate = calQ.data?.base_rate ?? types.find((t) => t.id === activeTypeId)?.base_rate ?? "0";
  const loading = hotelsQ.isLoading || typesQ.isLoading || calQ.isLoading;

  return (
    <div className="flex flex-col">
      <header className="flex h-14 items-center gap-4 border-b bg-background px-6">
        <SidebarTrigger />
        <Separator orientation="vertical" className="h-6" />
        <h1 className="text-lg font-semibold">Dynamic rates</h1>
        <div className="ml-auto flex items-center gap-2">
          <ConnectionStatus />
          <ThemeToggle />
          <ChatToggleButton />
        </div>
      </header>

      <div className="flex-1 space-y-6 p-6">
        <HotelOpsHeader
          title="Advance room pricing"
          subtitle="Set monthly and date-range rates ahead of time. Night audit and check-in pick the matching price for each night automatically."
          perms={perms}
          actions={
            <Button asChild variant="outline" size="sm" className="gap-1.5">
              <Link href="/app/hotel/inventory">
                <ArrowLeft className="h-3.5 w-3.5" />
                Inventory
              </Link>
            </Button>
          }
        />

        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1.5">
            <Label>Room type</Label>
            <Select value={activeTypeId} onValueChange={setRoomTypeId}>
              <SelectTrigger className="w-[220px]">
                <SelectValue placeholder="Select type" />
              </SelectTrigger>
              <SelectContent>
                {types.map((rt) => (
                  <SelectItem key={rt.id} value={rt.id}>
                    {rt.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center gap-1">
            <Button variant="outline" size="icon" onClick={() => shiftMonth(-1)} aria-label="Previous month">
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <div className="min-w-[160px] text-center text-sm font-medium">{monthLabel}</div>
            <Button variant="outline" size="icon" onClick={() => shiftMonth(1)} aria-label="Next month">
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
          <div className="ml-auto flex flex-wrap gap-2">
            {canEdit ? (
              <>
                <Button
                  variant="outline"
                  onClick={() => {
                    setMonthRate(String(baseRate));
                    setMonthOpen(true);
                  }}
                >
                  Set month rate
                </Button>
                <Button
                  onClick={() => {
                    setRangeForm({
                      start_date: start,
                      end_date: start,
                      nightly_rate: String(baseRate),
                      name: "",
                    });
                    setRangeOpen(true);
                  }}
                >
                  Add date range
                </Button>
              </>
            ) : null}
          </div>
        </div>

        <div className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">{monthLabel} calendar</CardTitle>
              <CardDescription>
                Base rate {formatMoney(baseRate, currency)}. Highlighted days use a saved rate window.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {loading ? (
                <div className="flex h-64 items-center justify-center">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
              ) : (
                <div className="grid grid-cols-7 gap-1 text-center text-xs">
                  {WEEKDAYS.map((d) => (
                    <div key={d} className="py-1 font-medium text-muted-foreground">
                      {d}
                    </div>
                  ))}
                  {cells.map((cell, idx) => {
                    if (cell.day == null) {
                      return <div key={`e-${idx}`} className="min-h-[72px] rounded-md bg-muted/20" />;
                    }
                    const planned = cell.night?.source === "rate_plan";
                    return (
                      <div
                        key={cell.iso}
                        className={`flex min-h-[72px] flex-col items-center justify-between rounded-md border p-1.5 ${
                          planned
                            ? "border-emerald-500/40 bg-emerald-500/10"
                            : "border-border/60 bg-background"
                        }`}
                      >
                        <span className="self-start text-[11px] text-muted-foreground">{cell.day}</span>
                        <span className="text-sm font-semibold tabular-nums">
                          {formatMoney(cell.night?.rate ?? baseRate, currency)}
                        </span>
                        <span className="text-[10px] text-muted-foreground">
                          {planned ? "Plan" : "Base"}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">Saved windows</CardTitle>
              <CardDescription>
                Shorter ranges outrank longer ones (e.g. Oct 1–10 beats all of October).
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {monthWindows.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No rate windows for this month. The room-type base rate applies until you add one.
                </p>
              ) : (
                monthWindows.map((plan) => (
                  <div
                    key={plan.id}
                    className="flex items-start justify-between gap-3 rounded-md border border-border/70 p-3"
                  >
                    <div className="min-w-0 space-y-1">
                      <div className="truncate text-sm font-medium">{plan.name}</div>
                      <div className="text-xs text-muted-foreground">
                        {plan.start_date} → {plan.end_date}
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-semibold tabular-nums">
                          {formatMoney(plan.nightly_rate, currency)}
                        </span>
                        <Badge variant="secondary">Priority {plan.priority ?? 0}</Badge>
                      </div>
                    </div>
                    {canEdit ? (
                      <Button
                        size="icon"
                        variant="ghost"
                        onClick={() => removeWindow(plan.id, plan.name)}
                        aria-label={`Delete ${plan.name}`}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    ) : null}
                  </div>
                ))
              )}
              <p className="text-xs text-muted-foreground">
                Tip: set each month of the year in advance, then overlay holiday or event ranges as needed.
              </p>
            </CardContent>
          </Card>
        </div>
      </div>

      <Dialog open={monthOpen} onOpenChange={setMonthOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Set rate for {monthLabel}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              Applies {start} through {end} for the selected room type. Date-range rates inside this month
              still win when their priority is higher.
            </p>
            <div className="space-y-1.5">
              <Label>Nightly rate ({currency})</Label>
              <Input value={monthRate} onChange={(e) => setMonthRate(e.target.value)} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setMonthOpen(false)}>
              Cancel
            </Button>
            <Button onClick={saveMonthRate} disabled={busy || !monthRate}>
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : "Save month"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={rangeOpen} onOpenChange={setRangeOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add date-range rate</DialogTitle>
          </DialogHeader>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label>Start date</Label>
              <Input
                type="date"
                value={rangeForm.start_date}
                onChange={(e) => setRangeForm({ ...rangeForm, start_date: e.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label>End date</Label>
              <Input
                type="date"
                value={rangeForm.end_date}
                onChange={(e) => setRangeForm({ ...rangeForm, end_date: e.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label>Nightly rate ({currency})</Label>
              <Input
                value={rangeForm.nightly_rate}
                onChange={(e) => setRangeForm({ ...rangeForm, nightly_rate: e.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label>Name (optional)</Label>
              <Input
                value={rangeForm.name}
                onChange={(e) => setRangeForm({ ...rangeForm, name: e.target.value })}
                placeholder="e.g. Early October promo"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRangeOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={saveRangeRate}
              disabled={busy || !rangeForm.nightly_rate || !rangeForm.start_date || !rangeForm.end_date}
            >
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : "Save range"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
