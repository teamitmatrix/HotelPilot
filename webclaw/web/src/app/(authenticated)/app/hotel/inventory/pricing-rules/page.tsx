"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
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
import { Switch } from "@/components/ui/switch";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { Separator } from "@/components/ui/separator";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";
import { HotelOpsHeader } from "@/components/hotel/hotel-ops-header";
import { useAuth } from "@/lib/auth";
import { useHotelPermissions } from "@/lib/rbac";
import { useToast } from "@/components/toast-provider";
import {
  deletePricingRule,
  evaluatePricing,
  getPricingDashboard,
  savePricingRule,
  togglePricingRule,
  type PricingRule,
} from "@/lib/pricing-api";
import { ArrowLeft, Loader2, Plus, Trash2 } from "lucide-react";

const WEEKDAYS = [
  { value: 0, label: "Mon" },
  { value: 1, label: "Tue" },
  { value: 2, label: "Wed" },
  { value: 3, label: "Thu" },
  { value: 4, label: "Fri" },
  { value: 5, label: "Sat" },
  { value: 6, label: "Sun" },
];

const EMPTY_FORM = {
  id: "",
  name: "",
  rule_kind: "occupancy" as "occupancy" | "min_los",
  room_type_id: "all",
  priority: "20",
  occupancy_op: "gt",
  occupancy_pct: "80",
  within_hours: "",
  adjustment_pct: "15",
  min_nights: "2",
  days_of_week: [5, 6] as number[],
  is_active: true,
};

function formatStamp(value?: string) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

export default function PricingRulesPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const dashQ = useQuery({
    queryKey: ["hotel-pricing-rules"],
    queryFn: getPricingDashboard,
    enabled: perms.canManageRooms,
  });
  const data = dashQ.data;

  const [hotelId, setHotelId] = useState("");
  const activeHotel = hotelId || data?.hotels[0]?.id || "";
  const roomTypes = useMemo(
    () => (data?.room_types || []).filter((rt) => !activeHotel || rt.hotel_id === activeHotel),
    [data, activeHotel],
  );
  const rules = useMemo(
    () => (data?.rules || []).filter((r) => !activeHotel || r.hotel_id === activeHotel),
    [data, activeHotel],
  );
  const yieldRates = useMemo(
    () => (data?.yield_rates || []).filter((r) => !activeHotel || r.hotel_id === activeHotel).slice(0, 12),
    [data, activeHotel],
  );
  const restrictions = useMemo(
    () =>
      (data?.stay_restrictions || [])
        .filter((r) => !activeHotel || r.hotel_id === activeHotel)
        .slice(0, 12),
    [data, activeHotel],
  );

  const [form, setForm] = useState(EMPTY_FORM);
  const [busy, setBusy] = useState(false);

  function loadRule(rule: PricingRule) {
    setForm({
      id: rule.id,
      name: rule.name,
      rule_kind: rule.rule_kind === "min_los" ? "min_los" : "occupancy",
      room_type_id: rule.room_type_id || "all",
      priority: String(rule.priority ?? 10),
      occupancy_op: rule.occupancy_op || "gt",
      occupancy_pct: String(rule.occupancy_pct ?? "80"),
      within_hours: rule.within_hours != null ? String(rule.within_hours) : "",
      adjustment_pct: String(rule.adjustment_pct ?? "15"),
      min_nights: String(rule.min_nights ?? 2),
      days_of_week: rule.days_of_week?.length ? [...rule.days_of_week] : [5, 6],
      is_active: !!rule.is_active,
    });
  }

  function resetForm() {
    setForm(EMPTY_FORM);
  }

  function toggleDay(day: number) {
    setForm((f) => {
      const has = f.days_of_week.includes(day);
      return {
        ...f,
        days_of_week: has
          ? f.days_of_week.filter((d) => d !== day)
          : [...f.days_of_week, day].sort((a, b) => a - b),
      };
    });
  }

  async function onSave() {
    if (!activeHotel || !form.name.trim()) return;
    setBusy(true);
    try {
      const body: Record<string, unknown> = {
        hotel_id: activeHotel,
        name: form.name.trim(),
        rule_kind: form.rule_kind,
        room_type_id: form.room_type_id === "all" ? "" : form.room_type_id,
        priority: form.priority,
        is_active: form.is_active,
      };
      if (form.id) body.id = form.id;
      if (form.rule_kind === "occupancy") {
        body.occupancy_op = form.occupancy_op;
        body.occupancy_pct = form.occupancy_pct;
        body.adjustment_pct = form.adjustment_pct;
        if (form.within_hours !== "") body.within_hours = Number(form.within_hours);
      } else {
        body.min_nights = Number(form.min_nights);
        body.days_of_week = form.days_of_week;
      }
      await savePricingRule(body);
      showToast({ type: "success", message: form.id ? "Rule updated" : "Rule created" });
      resetForm();
      await queryClient.invalidateQueries({ queryKey: ["hotel-pricing-rules"] });
    } catch (err) {
      showToast({ type: "error", message: err instanceof Error ? err.message : "Save failed" });
    } finally {
      setBusy(false);
    }
  }

  async function onEvaluate() {
    setBusy(true);
    try {
      const result = await evaluatePricing(activeHotel || undefined);
      const first = Array.isArray(result.results) ? (result.results[0] as Record<string, unknown>) : null;
      const written = first ? Number(first.rates_written || 0) : 0;
      showToast({
        type: "success",
        message: `Yield run finished — ${written} rate${written === 1 ? "" : "s"} updated and pushed to channels`,
      });
      await queryClient.invalidateQueries({ queryKey: ["hotel-pricing-rules"] });
    } catch (err) {
      showToast({ type: "error", message: err instanceof Error ? err.message : "Evaluate failed" });
    } finally {
      setBusy(false);
    }
  }

  if (!perms.canManageRooms) {
    return (
      <div className="p-6 text-sm text-muted-foreground">
        You do not have permission to manage pricing rules.
      </div>
    );
  }

  return (
    <div className="flex flex-col">
      <header className="flex h-14 items-center gap-4 border-b bg-background px-6">
        <SidebarTrigger />
        <Separator orientation="vertical" className="h-6" />
        <h1 className="text-lg font-semibold">Dynamic pricing</h1>
        <div className="ml-auto flex items-center gap-2">
          <ConnectionStatus />
          <ThemeToggle />
          <ChatToggleButton />
        </div>
      </header>

      <div className="flex-1 space-y-6 p-6">
        <HotelOpsHeader
          title="Pricing rules"
          subtitle="Occupancy triggers and weekend length-of-stay rules. Daily yield updates feed the channel manager."
          perms={perms}
          actions={
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" asChild>
                <Link href="/app/hotel/inventory">
                  <ArrowLeft className="mr-1.5 h-4 w-4" />
                  Inventory
                </Link>
              </Button>
              <Button disabled={busy || !activeHotel} onClick={onEvaluate}>
                {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                Run yield now
              </Button>
            </div>
          }
        />

        {(data?.hotels.length || 0) > 1 ? (
          <div className="max-w-xs">
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

        {dashQ.isLoading ? (
          <div className="flex h-40 items-center justify-center">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <div className="grid gap-6 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)]">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">
                  {form.id ? "Edit rule" : "Create rule"}
                </CardTitle>
                <CardDescription>
                  Higher priority wins when several occupancy rules match the same night.
                  Horizon: {data?.horizon_days ?? 14} days.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="sm:col-span-2">
                    <Label>Name</Label>
                    <Input
                      value={form.name}
                      onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                      placeholder="High occupancy uplift"
                    />
                  </div>
                  <div>
                    <Label>Rule type</Label>
                    <Select
                      value={form.rule_kind}
                      onValueChange={(rule_kind: "occupancy" | "min_los") =>
                        setForm((f) => ({ ...f, rule_kind }))
                      }
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="occupancy">Occupancy adjustment</SelectItem>
                        <SelectItem value="min_los">Minimum length of stay</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div>
                    <Label>Room type</Label>
                    <Select
                      value={form.room_type_id}
                      onValueChange={(room_type_id) => setForm((f) => ({ ...f, room_type_id }))}
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">All room types</SelectItem>
                        {roomTypes.map((rt) => (
                          <SelectItem key={rt.id} value={rt.id}>
                            {rt.name} ({rt.code})
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div>
                    <Label>Priority</Label>
                    <Input
                      type="number"
                      value={form.priority}
                      onChange={(e) => setForm((f) => ({ ...f, priority: e.target.value }))}
                    />
                  </div>
                  <div className="flex items-end gap-2 pb-1">
                    <Switch
                      checked={form.is_active}
                      onCheckedChange={(is_active) => setForm((f) => ({ ...f, is_active }))}
                    />
                    <Label>Active</Label>
                  </div>
                </div>

                {form.rule_kind === "occupancy" ? (
                  <div className="grid gap-4 rounded-md border p-4 sm:grid-cols-2">
                    <div>
                      <Label>If occupancy</Label>
                      <div className="mt-1 flex gap-2">
                        <Select
                          value={form.occupancy_op}
                          onValueChange={(occupancy_op) => setForm((f) => ({ ...f, occupancy_op }))}
                        >
                          <SelectTrigger className="w-24">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="gt">&gt;</SelectItem>
                            <SelectItem value="gte">≥</SelectItem>
                            <SelectItem value="lt">&lt;</SelectItem>
                            <SelectItem value="lte">≤</SelectItem>
                          </SelectContent>
                        </Select>
                        <Input
                          type="number"
                          value={form.occupancy_pct}
                          onChange={(e) => setForm((f) => ({ ...f, occupancy_pct: e.target.value }))}
                          placeholder="80"
                        />
                        <span className="self-center text-sm text-muted-foreground">%</span>
                      </div>
                    </div>
                    <div>
                      <Label>Adjust base rate by</Label>
                      <div className="mt-1 flex gap-2">
                        <Input
                          type="number"
                          value={form.adjustment_pct}
                          onChange={(e) => setForm((f) => ({ ...f, adjustment_pct: e.target.value }))}
                          placeholder="15"
                        />
                        <span className="self-center text-sm text-muted-foreground">%</span>
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">
                        Use a negative value for a discount (e.g. -10).
                      </p>
                    </div>
                    <div className="sm:col-span-2">
                      <Label>Only within hours of arrival (optional)</Label>
                      <Input
                        type="number"
                        value={form.within_hours}
                        onChange={(e) => setForm((f) => ({ ...f, within_hours: e.target.value }))}
                        placeholder="48"
                      />
                      <p className="mt-1 text-xs text-muted-foreground">
                        Example: last-minute discount when occupancy is low within 48 hours.
                      </p>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-4 rounded-md border p-4">
                    <div className="max-w-xs">
                      <Label>Minimum nights</Label>
                      <Input
                        type="number"
                        min={1}
                        value={form.min_nights}
                        onChange={(e) => setForm((f) => ({ ...f, min_nights: e.target.value }))}
                      />
                    </div>
                    <div>
                      <Label>Applies on</Label>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {WEEKDAYS.map((day) => {
                          const on = form.days_of_week.includes(day.value);
                          return (
                            <Button
                              key={day.value}
                              type="button"
                              size="sm"
                              variant={on ? "default" : "outline"}
                              onClick={() => toggleDay(day.value)}
                            >
                              {day.label}
                            </Button>
                          );
                        })}
                      </div>
                    </div>
                  </div>
                )}

                <div className="flex flex-wrap gap-2">
                  <Button disabled={busy || !form.name.trim()} onClick={onSave}>
                    {form.id ? "Update rule" : "Add rule"}
                  </Button>
                  {form.id ? (
                    <Button variant="outline" onClick={resetForm}>
                      Cancel edit
                    </Button>
                  ) : (
                    <Button
                      variant="outline"
                      className="gap-1.5"
                      onClick={() =>
                        setForm({
                          ...EMPTY_FORM,
                          name: "High occupancy uplift",
                          occupancy_op: "gt",
                          occupancy_pct: "80",
                          adjustment_pct: "15",
                        })
                      }
                    >
                      <Plus className="h-3.5 w-3.5" />
                      Occupancy template
                    </Button>
                  )}
                </div>
              </CardContent>
            </Card>

            <div className="space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Active rules</CardTitle>
                  <CardDescription>Toggle, reorder by priority, or edit.</CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  {rules.length === 0 ? (
                    <p className="text-sm text-muted-foreground">No pricing rules yet.</p>
                  ) : (
                    rules.map((rule) => (
                      <div
                        key={rule.id}
                        className="flex flex-wrap items-start justify-between gap-3 rounded-md border p-3"
                      >
                        <div className="min-w-0 flex-1 space-y-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-medium">{rule.name}</span>
                            <Badge variant="outline">P{rule.priority}</Badge>
                            <Badge variant={rule.rule_kind === "min_los" ? "secondary" : "default"}>
                              {rule.rule_kind === "min_los" ? "Min stay" : "Occupancy"}
                            </Badge>
                            {!rule.is_active ? <Badge variant="outline">Off</Badge> : null}
                          </div>
                          <p className="text-sm text-muted-foreground">{rule.summary}</p>
                          <p className="text-xs text-muted-foreground">
                            {rule.room_type_name || "All room types"}
                          </p>
                        </div>
                        <div className="flex items-center gap-2">
                          <Switch
                            checked={rule.is_active}
                            onCheckedChange={async (on) => {
                              try {
                                await togglePricingRule(rule.id, on);
                                await queryClient.invalidateQueries({
                                  queryKey: ["hotel-pricing-rules"],
                                });
                              } catch (err) {
                                showToast({
                                  type: "error",
                                  message: err instanceof Error ? err.message : "Toggle failed",
                                });
                              }
                            }}
                          />
                          <Button size="sm" variant="outline" onClick={() => loadRule(rule)}>
                            Edit
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={async () => {
                              if (!confirm(`Delete “${rule.name}”?`)) return;
                              await deletePricingRule(rule.id);
                              await queryClient.invalidateQueries({
                                queryKey: ["hotel-pricing-rules"],
                              });
                            }}
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </div>
                      </div>
                    ))
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Latest yield rates</CardTitle>
                  <CardDescription>
                    Written into hospitality rate plans and pushed to OTAs.
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  {yieldRates.length === 0 ? (
                    <p className="text-sm text-muted-foreground">No yield rates yet — run yield.</p>
                  ) : (
                    <ul className="space-y-2 text-sm">
                      {yieldRates.map((row) => (
                        <li key={row.id} className="flex justify-between gap-2 border-b pb-2 last:border-0">
                          <span>
                            {row.start_date} · {row.code}
                          </span>
                          <span className="font-medium">{row.nightly_rate}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Upcoming stay restrictions</CardTitle>
                </CardHeader>
                <CardContent>
                  {restrictions.length === 0 ? (
                    <p className="text-sm text-muted-foreground">No length-of-stay restrictions.</p>
                  ) : (
                    <ul className="space-y-2 text-sm">
                      {restrictions.map((row) => (
                        <li key={row.id} className="flex justify-between gap-2 border-b pb-2 last:border-0">
                          <span>{row.stay_date}</span>
                          <span>Min {row.min_nights} night{row.min_nights === 1 ? "" : "s"}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </CardContent>
              </Card>

              {(data?.recent_runs.length || 0) > 0 ? (
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">Recent runs</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-2 text-sm">
                    {data?.recent_runs
                      .filter((run) => !activeHotel || run.hotel_id === activeHotel)
                      .slice(0, 5)
                      .map((run) => (
                        <div key={run.id} className="flex justify-between gap-2">
                          <span>
                            {run.business_date} · {String(run.summary.rates_written ?? 0)} rates
                          </span>
                          <span className="text-muted-foreground">{formatStamp(run.created_at)}</span>
                        </div>
                      ))}
                  </CardContent>
                </Card>
              ) : null}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
