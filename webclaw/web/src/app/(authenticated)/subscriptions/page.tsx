"use client";

import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CalendarPlus,
  CreditCard,
  Layers,
  Percent,
  History,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import Link from "next/link";
import {
  applyBillingCoupon,
  applyBillingOverride,
  createBillingCoupon,
  createBillingCredit,
  getBillingOverview,
  getSubscriptionHistory,
  listBillingInvoices,
  listPlans,
  listTenants,
  updatePlan,
  type FeatureToggle,
  type HotelTenant,
  type StripeInvoice,
  type SubscriptionEvent,
  type SubscriptionPlan,
} from "@/lib/admin-api";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";

function FeatureSwitch({
  enabled,
  onToggle,
  label,
  description,
}: {
  enabled: boolean;
  onToggle: () => void;
  label: string;
  description?: string;
}) {
  return (
    <button
      type="button"
      aria-pressed={enabled}
      onClick={onToggle}
      className="flex w-full items-start justify-between gap-3 rounded-md border px-3 py-2 text-left text-sm transition-colors hover:bg-accent"
    >
      <span>
        <span className="block font-medium">{label}</span>
        {description ? (
          <span className="block text-xs text-muted-foreground">{description}</span>
        ) : null}
      </span>
      <span
        className={`mt-0.5 inline-flex h-5 w-9 shrink-0 items-center rounded-full px-0.5 transition-colors ${
          enabled ? "bg-primary" : "bg-muted"
        }`}
      >
        <span
          className={`h-4 w-4 rounded-full bg-background shadow-sm transition-transform ${
            enabled ? "translate-x-4" : "translate-x-0"
          }`}
        />
      </span>
    </button>
  );
}

function formatDate(iso?: string | null) {
  if (!iso) return "—";
  const t = Date.parse(iso.includes("T") ? iso : iso.replace(" ", "T") + "Z");
  if (Number.isNaN(t)) return iso;
  return new Date(t).toLocaleString();
}

function eventLabel(type: string) {
  switch (type) {
    case "extend_trial":
      return "Trial extended";
    case "apply_discount":
      return "Discount applied";
    case "clear_discount":
      return "Discount cleared";
    case "clear_trial":
      return "Trial cleared";
    case "plan_change":
      return "Plan changed";
    default:
      return type.replace(/_/g, " ");
  }
}

export default function SubscriptionsPage() {
  const queryClient = useQueryClient();
  const searchParams = useSearchParams();
  const { data, isLoading, error } = useQuery({
    queryKey: ["admin-plans"],
    queryFn: listPlans,
  });
  const { data: tenants = [] } = useQuery({
    queryKey: ["admin-tenants"],
    queryFn: listTenants,
  });
  const { data: billingOverview } = useQuery({
    queryKey: ["billing-overview"],
    queryFn: getBillingOverview,
    refetchInterval: 60_000,
  });

  const [savingId, setSavingId] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [selectedTenantId, setSelectedTenantId] = useState<string>("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [overrideOpen, setOverrideOpen] = useState(false);
  const [events, setEvents] = useState<SubscriptionEvent[]>([]);
  const [trialDays, setTrialDays] = useState("14");
  const [discountPct, setDiscountPct] = useState("10");
  const [discountNote, setDiscountNote] = useState("");
  const [busyOverride, setBusyOverride] = useState(false);
  const [opsTenantId, setOpsTenantId] = useState("");
  const [invoices, setInvoices] = useState<StripeInvoice[]>([]);
  const [creditCents, setCreditCents] = useState("1000");
  const [couponCode, setCouponCode] = useState("");
  const [couponPercent, setCouponPercent] = useState("10");
  const [opsBusy, setOpsBusy] = useState(false);

  useEffect(() => {
    document.title = "Subscriptions & Plans — HotelPilot Central Admin";
  }, []);

  useEffect(() => {
    if (searchParams.get("new") === "1") {
      setMessage("Edit the Starter, Professional, or Enterprise tier cards below to configure pricing and limits.");
    }
  }, [searchParams]);

  const catalog: FeatureToggle[] = data?.feature_catalog ?? [];
  const plans = useMemo(() => {
    const list = data?.plans ?? [];
    // Prefer Starter → Professional(business) → Enterprise order
    const order = ["starter", "business", "enterprise"];
    return [...list].sort(
      (a, b) => order.indexOf(a.code) - order.indexOf(b.code) || a.sort_order - b.sort_order,
    );
  }, [data?.plans]);

  const selectedTenant = tenants.find((t) => t.id === selectedTenantId) || null;

  async function save(plan: SubscriptionPlan, patch: Partial<SubscriptionPlan>) {
    setSavingId(plan.id);
    setMessage("");
    try {
      await updatePlan(plan.id, { ...plan, ...patch });
      await queryClient.invalidateQueries({ queryKey: ["admin-plans"] });
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setSavingId(null);
    }
  }

  async function loadHistory(tenant: HotelTenant) {
    setSelectedTenantId(tenant.id);
    setHistoryOpen(true);
    try {
      const res = await getSubscriptionHistory(tenant.id);
      setEvents(res.events);
    } catch (e) {
      setMessage((e as Error).message);
      setEvents([]);
    }
  }

  async function openOverride(tenant: HotelTenant) {
    setSelectedTenantId(tenant.id);
    setDiscountPct(String(tenant.discount_percent || 10));
    setDiscountNote(tenant.discount_note || "");
    setOverrideOpen(true);
  }

  async function runOverride(
    action: "extend_trial" | "apply_discount" | "clear_discount" | "clear_trial",
  ) {
    if (!selectedTenantId) return;
    setBusyOverride(true);
    setMessage("");
    try {
      const res = await applyBillingOverride(selectedTenantId, {
        action,
        days: Number(trialDays) || 14,
        discount_percent: Number(discountPct) || 0,
        note: discountNote,
      });
      setMessage(res.message);
      await queryClient.invalidateQueries({ queryKey: ["admin-tenants"] });
      const hist = await getSubscriptionHistory(selectedTenantId);
      setEvents(hist.events);
      if (action === "extend_trial" || action === "clear_trial") {
        /* keep dialog open so operator can see result */
      } else if (action === "apply_discount") {
        setOverrideOpen(false);
      }
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusyOverride(false);
    }
  }

  return (
    <div className="flex flex-col">
      <header className="flex h-14 items-center gap-4 border-b bg-background px-6">
        <SidebarTrigger />
        <Separator orientation="vertical" className="h-6" />
        <h1 className="text-lg font-semibold">Subscriptions & Plans</h1>
        <div className="ml-auto flex items-center gap-2">
          <ConnectionStatus />
          <ThemeToggle />
          <ChatToggleButton />
        </div>
      </header>

      <div className="flex-1 space-y-8 p-6">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">Billing operations</h2>
          <p className="text-muted-foreground">
            Past-due Stripe subscriptions, recent payment failures, and trials ending soon.
          </p>
        </div>

        <div className="grid gap-4 md:grid-cols-3">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                At-risk subscriptions
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-3xl font-bold">{billingOverview?.counts.at_risk ?? 0}</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Failed payment events
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-3xl font-bold">{billingOverview?.counts.failed_events ?? 0}</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Trials ending (7 days)
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-3xl font-bold">{billingOverview?.counts.trials_ending ?? 0}</p>
            </CardContent>
          </Card>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Past-due / unpaid</CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Hotel</TableHead>
                    <TableHead>Stripe</TableHead>
                    <TableHead>Updated</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(billingOverview?.at_risk || []).length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={3} className="text-muted-foreground">
                        No at-risk subscriptions.
                      </TableCell>
                    </TableRow>
                  ) : (
                    (billingOverview?.at_risk || []).map((row) => (
                      <TableRow key={row.tenant_id}>
                        <TableCell>
                          <Link
                            href={`/properties/${row.tenant_id}`}
                            className="font-medium text-primary hover:underline"
                          >
                            {row.hotel_name}
                          </Link>
                          <p className="text-xs text-muted-foreground">{row.plan_code}</p>
                        </TableCell>
                        <TableCell>
                          <Badge variant="destructive">{row.stripe_status}</Badge>
                        </TableCell>
                        <TableCell className="text-muted-foreground">
                          {formatDate(row.updated_at)}
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Recent failures & trials</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <p className="mb-2 text-xs font-medium uppercase text-muted-foreground">
                  Payment failures
                </p>
                <Table>
                  <TableBody>
                    {(billingOverview?.failed_events || []).slice(0, 6).length === 0 ? (
                      <TableRow>
                        <TableCell className="text-muted-foreground">
                          No recent payment failures.
                        </TableCell>
                      </TableRow>
                    ) : (
                      (billingOverview?.failed_events || []).slice(0, 6).map((ev) => (
                        <TableRow key={ev.id}>
                          <TableCell>
                            <Link
                              href={`/properties/${ev.tenant_id}`}
                              className="font-medium text-primary hover:underline"
                            >
                              {ev.hotel_name}
                            </Link>
                            <p className="text-xs text-muted-foreground">
                              {ev.event_type.replace(/_/g, " ")} · {formatDate(ev.created_at)}
                            </p>
                          </TableCell>
                        </TableRow>
                      ))
                    )}
                  </TableBody>
                </Table>
              </div>
              <div>
                <p className="mb-2 text-xs font-medium uppercase text-muted-foreground">
                  Trials ending
                </p>
                <Table>
                  <TableBody>
                    {(billingOverview?.trials_ending || []).length === 0 ? (
                      <TableRow>
                        <TableCell className="text-muted-foreground">
                          No trials ending in the next 7 days.
                        </TableCell>
                      </TableRow>
                    ) : (
                      (billingOverview?.trials_ending || []).map((t) => (
                        <TableRow key={t.tenant_id}>
                          <TableCell>
                            <Link
                              href={`/properties/${t.tenant_id}`}
                              className="font-medium text-primary hover:underline"
                            >
                              {t.hotel_name}
                            </Link>
                            <p className="text-xs text-muted-foreground">
                              Ends {formatDate(t.trial_ends_at)}
                            </p>
                          </TableCell>
                        </TableRow>
                      ))
                    )}
                  </TableBody>
                </Table>
              </div>
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Billing operations</CardTitle>
            <p className="text-sm text-muted-foreground">
              Stripe invoices, credits, and coupons for a selected hotel (requires Stripe link).
            </p>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap gap-3 items-end">
              <div className="min-w-[220px] space-y-1">
                <Label>Hotel</Label>
                <Select
                  value={opsTenantId}
                  onValueChange={(v) => {
                    setOpsTenantId(v);
                    setInvoices([]);
                  }}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select hotel" />
                  </SelectTrigger>
                  <SelectContent>
                    {tenants.map((t) => (
                      <SelectItem key={String(t.id)} value={String(t.id)}>
                        {t.hotel_name || t.id}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Button
                variant="outline"
                disabled={opsBusy || !opsTenantId}
                onClick={async () => {
                  setOpsBusy(true);
                  setMessage("");
                  try {
                    setInvoices(await listBillingInvoices(opsTenantId));
                  } catch (e) {
                    setMessage((e as Error).message);
                  } finally {
                    setOpsBusy(false);
                  }
                }}
              >
                Load invoices
              </Button>
            </div>

            {invoices.length > 0 && (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Number</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Amount</TableHead>
                    <TableHead />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {invoices.map((inv) => (
                    <TableRow key={inv.id}>
                      <TableCell>{inv.number || inv.id}</TableCell>
                      <TableCell>
                        <Badge variant="outline">{inv.status || "—"}</Badge>
                      </TableCell>
                      <TableCell>
                        {inv.amount_due != null
                          ? `${((inv.amount_due || 0) / 100).toFixed(2)} ${inv.currency || ""}`
                          : "—"}
                      </TableCell>
                      <TableCell>
                        {inv.hosted_invoice_url ? (
                          <a
                            href={inv.hosted_invoice_url}
                            target="_blank"
                            rel="noreferrer"
                            className="text-sm text-primary hover:underline"
                          >
                            Open
                          </a>
                        ) : null}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}

            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-2 rounded-md border p-3">
                <p className="text-sm font-medium">Apply credit (cents)</p>
                <div className="flex gap-2">
                  <Input value={creditCents} onChange={(e) => setCreditCents(e.target.value)} />
                  <Button
                    disabled={opsBusy || !opsTenantId}
                    onClick={async () => {
                      setOpsBusy(true);
                      setMessage("");
                      try {
                        await createBillingCredit({
                          tenant_id: opsTenantId,
                          amount_cents: Number(creditCents) || 0,
                        });
                        setMessage("Credit applied");
                      } catch (e) {
                        setMessage((e as Error).message);
                      } finally {
                        setOpsBusy(false);
                      }
                    }}
                  >
                    Credit
                  </Button>
                </div>
              </div>
              <div className="space-y-2 rounded-md border p-3">
                <p className="text-sm font-medium">Coupon</p>
                <div className="flex flex-wrap gap-2">
                  <Input
                    placeholder="CODE"
                    value={couponCode}
                    onChange={(e) => setCouponCode(e.target.value)}
                  />
                  <Input
                    className="w-20"
                    placeholder="%"
                    value={couponPercent}
                    onChange={(e) => setCouponPercent(e.target.value)}
                  />
                  <Button
                    variant="outline"
                    disabled={opsBusy || !couponCode}
                    onClick={async () => {
                      setOpsBusy(true);
                      setMessage("");
                      try {
                        await createBillingCoupon({
                          code: couponCode,
                          percent_off: Number(couponPercent) || undefined,
                        });
                        setMessage("Coupon created in Stripe");
                      } catch (e) {
                        setMessage((e as Error).message);
                      } finally {
                        setOpsBusy(false);
                      }
                    }}
                  >
                    Create
                  </Button>
                  <Button
                    disabled={opsBusy || !opsTenantId || !couponCode}
                    onClick={async () => {
                      setOpsBusy(true);
                      setMessage("");
                      try {
                        await applyBillingCoupon({
                          tenant_id: opsTenantId,
                          code: couponCode,
                        });
                        setMessage("Coupon applied to subscription");
                      } catch (e) {
                        setMessage((e as Error).message);
                      } finally {
                        setOpsBusy(false);
                      }
                    }}
                  >
                    Apply
                  </Button>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>

        <div>
          <h2 className="text-2xl font-bold tracking-tight">Tier Builder</h2>
          <p className="text-muted-foreground">
            Configure Starter, Professional, and Enterprise — pricing, room/staff caps, and feature toggles.
          </p>
        </div>

        {message && (
          <div className="rounded-md border border-primary/20 bg-primary/5 p-3 text-sm">{message}</div>
        )}
        {error && (
          <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
            {(error as Error).message}
          </div>
        )}
        {isLoading && <p className="text-sm text-muted-foreground">Loading plans…</p>}

        <div className="grid gap-4 lg:grid-cols-3">
          {plans.map((plan) => (
            <Card key={plan.id}>
              <CardHeader className="space-y-1">
                <div className="flex items-center justify-between gap-2">
                  <CardTitle className="flex items-center gap-2 text-lg">
                    <Layers className="h-4 w-4 text-primary" />
                    {plan.name}
                  </CardTitle>
                  <Badge variant={plan.is_active ? "default" : "secondary"}>
                    {plan.code === "business" ? "professional" : plan.code}
                  </Badge>
                </div>
                <p className="text-xs text-muted-foreground">
                  {plan.unlimited_rooms ? "Unlimited rooms" : `Up to ${plan.max_rooms} rooms`}
                  {" · "}
                  {plan.unlimited_staff
                    ? "Unlimited staff"
                    : `Up to ${plan.max_staff} staff`}
                </p>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label>Billing cycle</Label>
                    <Select
                      value={plan.billing_cycle}
                      onValueChange={(billing_cycle) =>
                        save(plan, { billing_cycle: billing_cycle as "monthly" | "annual" })
                      }
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="monthly">Monthly</SelectItem>
                        <SelectItem value="annual">Annual</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5">
                    <Label>Max rooms (0 = unlimited)</Label>
                    <Input
                      type="number"
                      min={0}
                      defaultValue={plan.max_rooms}
                      disabled={savingId === plan.id}
                      onBlur={(e) => {
                        const max_rooms = Number(e.target.value);
                        if (!Number.isNaN(max_rooms) && max_rooms !== plan.max_rooms) {
                          save(plan, { max_rooms });
                        }
                      }}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Monthly price</Label>
                    <Input
                      defaultValue={plan.monthly_price}
                      disabled={savingId === plan.id}
                      onBlur={(e) => {
                        if (e.target.value !== plan.monthly_price) {
                          save(plan, { monthly_price: e.target.value });
                        }
                      }}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Annual price</Label>
                    <Input
                      defaultValue={plan.annual_price}
                      disabled={savingId === plan.id}
                      onBlur={(e) => {
                        if (e.target.value !== plan.annual_price) {
                          save(plan, { annual_price: e.target.value });
                        }
                      }}
                    />
                  </div>
                  <div className="space-y-1.5 col-span-2">
                    <Label>Max staff accounts (0 = unlimited)</Label>
                    <Input
                      type="number"
                      min={0}
                      defaultValue={plan.max_staff ?? 5}
                      disabled={savingId === plan.id}
                      onBlur={(e) => {
                        const max_staff = Number(e.target.value);
                        if (!Number.isNaN(max_staff) && max_staff !== plan.max_staff) {
                          save(plan, { max_staff });
                        }
                      }}
                    />
                  </div>
                </div>

                <div className="space-y-2">
                  <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Feature toggles
                  </p>
                  <div className="space-y-2 max-h-72 overflow-y-auto pr-1">
                    {catalog.map((feature) => (
                      <FeatureSwitch
                        key={feature.key}
                        label={feature.label}
                        description={feature.description}
                        enabled={Boolean(plan.features?.[feature.key])}
                        onToggle={() =>
                          save(plan, {
                            features: {
                              ...plan.features,
                              [feature.key]: !plan.features?.[feature.key],
                            },
                          })
                        }
                      />
                    ))}
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>

        {/* Tenant subscription history & overrides */}
        <div className="space-y-4">
          <div>
            <h2 className="text-xl font-bold tracking-tight">Tenant Subscriptions</h2>
            <p className="text-muted-foreground">
              Review history and apply manual overrides (extend trial, discount).
            </p>
          </div>

          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Hotel</TableHead>
                  <TableHead>Plan</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Trial / Discount</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {tenants.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={5} className="text-muted-foreground">
                      No hotel tenants yet.
                    </TableCell>
                  </TableRow>
                ) : (
                  tenants.map((tenant) => (
                    <TableRow key={tenant.id}>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <CreditCard className="h-4 w-4 text-muted-foreground" />
                          <div>
                            <p className="font-medium">{tenant.hotel_name}</p>
                            <p className="text-xs text-muted-foreground">{tenant.admin_email}</p>
                          </div>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline">{tenant.plan_name || tenant.plan_code}</Badge>
                      </TableCell>
                      <TableCell>
                        <Badge variant={tenant.status === "active" ? "default" : "secondary"}>
                          {tenant.status}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {tenant.trial_ends_at ? (
                          <span>Trial → {formatDate(tenant.trial_ends_at)}</span>
                        ) : (
                          <span>No trial</span>
                        )}
                        {tenant.discount_percent ? (
                          <span className="block">{tenant.discount_percent}% off</span>
                        ) : null}
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-1">
                          <Button size="sm" variant="outline" onClick={() => loadHistory(tenant)}>
                            <History className="h-3.5 w-3.5" />
                            History
                          </Button>
                          <Button size="sm" variant="outline" onClick={() => openOverride(tenant)}>
                            Override
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
        </div>
      </div>

      {/* History dialog */}
      <Dialog open={historyOpen} onOpenChange={setHistoryOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Subscription History</DialogTitle>
            <DialogDescription>
              Events for {selectedTenant?.hotel_name || "tenant"}.
            </DialogDescription>
          </DialogHeader>
          <div className="max-h-80 overflow-y-auto">
            {events.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted-foreground">
                No subscription events recorded yet.
              </p>
            ) : (
              <ul className="divide-y">
                {events.map((ev) => (
                  <li key={ev.id} className="py-3">
                    <p className="text-sm font-medium">{eventLabel(ev.event_type)}</p>
                    <p className="text-xs text-muted-foreground">{ev.detail}</p>
                    <p className="mt-1 text-[11px] text-muted-foreground">
                      {formatDate(ev.created_at)}
                      {ev.actor_email ? ` · ${ev.actor_email}` : ""}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* Override dialog */}
      <Dialog open={overrideOpen} onOpenChange={setOverrideOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Manual Override</DialogTitle>
            <DialogDescription>
              Extend trial or apply a discount for {selectedTenant?.hotel_name}.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="rounded-md border p-3 space-y-2">
              <p className="text-sm font-medium flex items-center gap-2">
                <CalendarPlus className="h-4 w-4" />
                Extend trial
              </p>
              <div className="flex items-end gap-2">
                <div className="flex-1 space-y-1.5">
                  <Label>Days</Label>
                  <Input
                    type="number"
                    min={1}
                    value={trialDays}
                    onChange={(e) => setTrialDays(e.target.value)}
                  />
                </div>
                <Button
                  disabled={busyOverride}
                  onClick={() => runOverride("extend_trial")}
                >
                  Extend
                </Button>
              </div>
              {selectedTenant?.trial_ends_at ? (
                <p className="text-xs text-muted-foreground">
                  Current trial ends {formatDate(selectedTenant.trial_ends_at)}
                </p>
              ) : null}
              <Button
                variant="ghost"
                size="sm"
                className="px-0"
                disabled={busyOverride || !selectedTenant?.trial_ends_at}
                onClick={() => runOverride("clear_trial")}
              >
                Clear trial
              </Button>
            </div>

            <div className="rounded-md border p-3 space-y-2">
              <p className="text-sm font-medium flex items-center gap-2">
                <Percent className="h-4 w-4" />
                Apply discount
              </p>
              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-1.5">
                  <Label>Percent</Label>
                  <Input
                    type="number"
                    min={0}
                    max={100}
                    value={discountPct}
                    onChange={(e) => setDiscountPct(e.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label>Note</Label>
                  <Input
                    value={discountNote}
                    onChange={(e) => setDiscountNote(e.target.value)}
                    placeholder="Partner rate"
                  />
                </div>
              </div>
              <div className="flex gap-2">
                <Button
                  disabled={busyOverride}
                  onClick={() => runOverride("apply_discount")}
                >
                  Apply discount
                </Button>
                <Button
                  variant="outline"
                  disabled={busyOverride}
                  onClick={() => runOverride("clear_discount")}
                >
                  Clear discount
                </Button>
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOverrideOpen(false)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
