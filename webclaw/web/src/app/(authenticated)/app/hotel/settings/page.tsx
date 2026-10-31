"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { PhoneInput } from "@/components/ui/phone-input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { Separator } from "@/components/ui/separator";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";
import { StaffManagementPanel } from "@/components/hotel/staff-management-panel";
import { useAuth } from "@/lib/auth";
import { useHotelPermissions } from "@/lib/rbac";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEntityList, useEntityMutation } from "@/lib/hooks";
import { useToast } from "@/components/toast-provider";
import {
  SKILL,
  type Hotel,
  itemsFrom,
  parseHotelSettings,
} from "@/lib/hospitality";
import {
  createCheckoutSession,
  createPortalSession,
  getSubscriptionStatus,
} from "@/lib/billing-api";
import { HotelNotificationsPanel } from "@/components/hotel/hotel-notifications-panel";
import { ChannelManagerPanel } from "@/components/hotel/channel-manager-panel";
import { Loader2 } from "lucide-react";

const TABS = ["property", "staff", "policies", "payments", "notifications", "channels"] as const;
type SettingsTab = (typeof TABS)[number];

function isSettingsTab(value: string | null): value is SettingsTab {
  return !!value && (TABS as readonly string[]).includes(value);
}

const PLAN_OPTIONS = [
  { code: "starter", label: "Starter" },
  { code: "business", label: "Professional" },
  { code: "enterprise", label: "Enterprise" },
];

export default function HotelSettingsPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const { showToast } = useToast();
  const mutate = useEntityMutation(SKILL);
  const queryClient = useQueryClient();
  const router = useRouter();
  const searchParams = useSearchParams();

  const tabParam = searchParams.get("tab");
  const billingFlash = searchParams.get("billing");
  const staffOnly = perms.canManageStaff && !perms.canManageHotel;
  const activeTab: SettingsTab = staffOnly
    ? "staff"
    : isSettingsTab(tabParam)
      ? tabParam
      : "property";

  const hotelsQ = useEntityList(
    SKILL,
    "list-hotels",
    { limit: "20" },
    { enabled: perms.canManageHotel },
  );
  const hotels = itemsFrom<Hotel>(hotelsQ.data, "hotels");
  const hotel = hotels[0] ?? null;
  const settings = useMemo(() => parseHotelSettings(hotel?.settings_json), [hotel?.settings_json]);

  const subscriptionQ = useQuery({
    queryKey: ["hotel-subscription"],
    queryFn: () => getSubscriptionStatus(),
    enabled: perms.canManageHotel && activeTab === "payments",
  });

  const [profile, setProfile] = useState({
    name: "",
    address: "",
    timezone: "UTC",
    currency: "USD",
    country: "",
    phone: "",
    email: "",
  });
  const [policies, setPolicies] = useState({
    check_in_time: "15:00",
    check_out_time: "11:00",
    cancellation_policy: "",
    night_audit_hour: "0",
    no_show_fee_nights: "1",
    no_show_fee_percent: "",
    no_show_fee_fixed: "",
  });
  const [payments, setPayments] = useState({
    payment_gateway: "none",
    payment_publishable_key: "",
    payment_secret_key: "",
    payment_webhook_secret: "",
    deposit_nights: "1",
    turnaround_days: "2",
  });
  const [upgradePlan, setUpgradePlan] = useState("business");
  const [billingBusy, setBillingBusy] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!hotel) return;
    setProfile({
      name: hotel.name || "",
      address: hotel.address || "",
      timezone: hotel.timezone || "UTC",
      currency: hotel.currency || "USD",
      country: hotel.country || "",
      phone: hotel.phone || "",
      email: hotel.email || "",
    });
    setPolicies({
      check_in_time: settings.check_in_time || "15:00",
      check_out_time: settings.check_out_time || "11:00",
      cancellation_policy: settings.cancellation_policy || "",
      night_audit_hour: settings.night_audit_hour || "0",
      no_show_fee_nights: settings.no_show_fee_nights || "1",
      no_show_fee_percent: settings.no_show_fee_percent || "",
      no_show_fee_fixed: settings.no_show_fee_fixed || "",
    });
    setPayments({
      payment_gateway: settings.payment_gateway || "none",
      payment_publishable_key: settings.payment_publishable_key || "",
      payment_secret_key: settings.payment_secret_key || "",
      payment_webhook_secret: settings.payment_webhook_secret || "",
      deposit_nights: settings.deposit_nights || "1",
      turnaround_days: settings.turnaround_days || "2",
    });
  }, [hotel, settings]);

  useEffect(() => {
    if (billingFlash === "cancelled") {
      showToast({ type: "info", message: "Stripe Checkout was cancelled — you can resume anytime." });
    }
  }, [billingFlash, showToast]);

  function setTab(next: string) {
    const tab = isSettingsTab(next) ? next : "property";
    router.replace(`/app/hotel/settings?tab=${tab}`);
  }

  async function saveHotel(patch: Record<string, string>) {
    if (!hotel?.id) {
      showToast({ type: "error", message: "No hotel found for this property" });
      return;
    }
    setSaving(true);
    try {
      await mutate.mutateAsync({
        action: "update-hotel",
        body: { id: hotel.id, ...patch },
      });
      await queryClient.invalidateQueries({ queryKey: ["owner-properties"] });
      await queryClient.invalidateQueries({ queryKey: ["hotel-payments-status"] });
      showToast({ type: "success", message: "Settings saved" });
    } catch (err) {
      showToast({
        type: "error",
        message: err instanceof Error ? err.message : "Save failed",
      });
    } finally {
      setSaving(false);
    }
  }

  async function startPlanCheckout() {
    setBillingBusy(true);
    try {
      const origin = window.location.origin;
      const session = await createCheckoutSession({
        plan_code: upgradePlan,
        billing_cycle: "monthly",
        success_url: `${origin}/app/hotel/dashboard?billing=success`,
        cancel_url: `${origin}/app/hotel/settings/payments?billing=cancelled`,
      });
      window.location.href = session.checkout_url;
    } catch (err) {
      showToast({
        type: "error",
        message: err instanceof Error ? err.message : "Could not start checkout",
      });
      setBillingBusy(false);
    }
  }

  async function openBillingPortal() {
    setBillingBusy(true);
    try {
      const portal = await createPortalSession({
        return_url: `${window.location.origin}/app/hotel/settings/payments`,
      });
      window.location.href = portal.portal_url;
    } catch (err) {
      showToast({
        type: "error",
        message: err instanceof Error ? err.message : "Billing portal unavailable",
      });
      setBillingBusy(false);
    }
  }

  if (!perms.canManageStaff && !perms.canManageHotel) {
    return (
      <div className="flex flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-2 border-b px-4">
          <SidebarTrigger className="-ml-1" />
          <Separator orientation="vertical" className="mr-2 h-4" />
          <h1 className="text-sm font-medium">Hotel settings</h1>
        </header>
        <div className="p-6 text-sm text-muted-foreground">
          Only hotel owners, admins, general managers, and branch managers can manage staff or settings.
        </div>
      </div>
    );
  }

  const sub = subscriptionQ.data;
  const subStatus = sub?.subscription?.status;
  const currentTier = String(sub?.tenant?.subscription_tier || "starter");

  return (
    <div className="flex flex-1 flex-col">
      <header className="flex h-14 shrink-0 items-center gap-2 border-b px-4">
        <SidebarTrigger className="-ml-1" />
        <Separator orientation="vertical" className="mr-2 h-4" />
        <h1 className="text-sm font-medium">Hotel settings</h1>
        <div className="ml-auto flex items-center gap-2">
          <ConnectionStatus />
          <ThemeToggle />
          <ChatToggleButton />
        </div>
      </header>

      <div className="flex-1 space-y-4 p-6">
        {hotelsQ.isLoading ? (
          <div className="flex h-40 items-center justify-center">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <Tabs value={activeTab} onValueChange={setTab}>
            <TabsList>
              {perms.canManageHotel && (
                <TabsTrigger value="property">Property</TabsTrigger>
              )}
              <TabsTrigger value="staff">Staff</TabsTrigger>
              {perms.canManageHotel && (
                <>
                  <TabsTrigger value="policies">Policies</TabsTrigger>
                  <TabsTrigger value="payments">Payments</TabsTrigger>
                  <TabsTrigger value="notifications">Notifications</TabsTrigger>
                  <TabsTrigger value="channels">Channels</TabsTrigger>
                </>
              )}
            </TabsList>

            <TabsContent value="property" className="mt-4">
              <Card>
                <CardHeader>
                  <CardTitle>Property profile</CardTitle>
                  <CardDescription>Name, location, and contact details for this hotel.</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="grid gap-4 sm:grid-cols-2">
                    <div className="sm:col-span-2">
                      <Label>Hotel name</Label>
                      <Input
                        value={profile.name}
                        onChange={(e) => setProfile((p) => ({ ...p, name: e.target.value }))}
                      />
                    </div>
                    <div className="sm:col-span-2">
                      <Label>Address</Label>
                      <Input
                        value={profile.address}
                        onChange={(e) => setProfile((p) => ({ ...p, address: e.target.value }))}
                      />
                    </div>
                    <div>
                      <Label>Timezone</Label>
                      <Input
                        value={profile.timezone}
                        onChange={(e) => setProfile((p) => ({ ...p, timezone: e.target.value }))}
                      />
                    </div>
                    <div>
                      <Label>Currency</Label>
                      <Input
                        value={profile.currency}
                        onChange={(e) => setProfile((p) => ({ ...p, currency: e.target.value }))}
                      />
                    </div>
                    <div>
                      <Label>Country</Label>
                      <Input
                        value={profile.country}
                        onChange={(e) => setProfile((p) => ({ ...p, country: e.target.value }))}
                      />
                    </div>
                    <div>
                      <Label>Phone</Label>
                      <PhoneInput
                        value={profile.phone}
                        onChange={(phone) => setProfile((p) => ({ ...p, phone }))}
                      />
                    </div>
                    <div className="sm:col-span-2">
                      <Label>Email</Label>
                      <Input
                        type="email"
                        value={profile.email}
                        onChange={(e) => setProfile((p) => ({ ...p, email: e.target.value }))}
                      />
                    </div>
                  </div>
                  <Button disabled={saving || !hotel} onClick={() => saveHotel(profile)}>
                    {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                    Save property
                  </Button>
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="staff" className="mt-4">
              <StaffManagementPanel />
            </TabsContent>

            <TabsContent value="policies" className="mt-4">
              <Card>
                <CardHeader>
                  <CardTitle>Stay policies</CardTitle>
                  <CardDescription>Check-in / check-out times and cancellation rules.</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="grid gap-4 sm:grid-cols-2">
                    <div>
                      <Label>Check-in time</Label>
                      <Input
                        value={policies.check_in_time}
                        onChange={(e) =>
                          setPolicies((p) => ({ ...p, check_in_time: e.target.value }))
                        }
                      />
                    </div>
                    <div>
                      <Label>Check-out time</Label>
                      <Input
                        value={policies.check_out_time}
                        onChange={(e) =>
                          setPolicies((p) => ({ ...p, check_out_time: e.target.value }))
                        }
                      />
                    </div>
                    <div className="sm:col-span-2">
                      <Label>Cancellation policy</Label>
                      <Textarea
                        rows={5}
                        value={policies.cancellation_policy}
                        onChange={(e) =>
                          setPolicies((p) => ({ ...p, cancellation_policy: e.target.value }))
                        }
                        placeholder="Free cancellation until 24 hours before arrival…"
                      />
                    </div>
                  </div>
                  <Separator />
                  <div>
                    <h3 className="text-sm font-medium">Auto no-show &amp; night audit</h3>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Night audit marks overdue arrivals as no-show and posts a fee. Precedence:
                      fixed amount → percent of nightly rate → nights × rate.
                    </p>
                  </div>
                  <div className="grid gap-4 sm:grid-cols-2">
                    <div>
                      <Label>Audit hour (hotel local)</Label>
                      <Input
                        type="number"
                        min={0}
                        max={23}
                        value={policies.night_audit_hour}
                        onChange={(e) =>
                          setPolicies((p) => ({ ...p, night_audit_hour: e.target.value }))
                        }
                      />
                    </div>
                    <div>
                      <Label>No-show fee (nights)</Label>
                      <Input
                        type="number"
                        min={0}
                        step="0.5"
                        value={policies.no_show_fee_nights}
                        onChange={(e) =>
                          setPolicies((p) => ({ ...p, no_show_fee_nights: e.target.value }))
                        }
                      />
                    </div>
                    <div>
                      <Label>No-show fee percent (optional)</Label>
                      <Input
                        type="number"
                        min={0}
                        max={100}
                        value={policies.no_show_fee_percent}
                        onChange={(e) =>
                          setPolicies((p) => ({ ...p, no_show_fee_percent: e.target.value }))
                        }
                        placeholder="e.g. 100"
                      />
                    </div>
                    <div>
                      <Label>No-show fixed fee (optional)</Label>
                      <Input
                        type="number"
                        min={0}
                        value={policies.no_show_fee_fixed}
                        onChange={(e) =>
                          setPolicies((p) => ({ ...p, no_show_fee_fixed: e.target.value }))
                        }
                        placeholder="Overrides nights / percent"
                      />
                    </div>
                  </div>
                  <Button
                    disabled={saving || !hotel}
                    onClick={() => saveHotel(policies)}
                  >
                    {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                    Save policies
                  </Button>
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="payments" className="mt-4 space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle>HotelPilot subscription</CardTitle>
                  <CardDescription>
                    SaaS billing for this property via Stripe Checkout. Failed renewals suspend
                    access until payment succeeds.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="text-muted-foreground">Current tier</span>
                    <Badge variant="outline">{currentTier}</Badge>
                    {subStatus ? (
                      <Badge
                        variant={
                          subStatus === "active" || subStatus === "trialing"
                            ? "default"
                            : "secondary"
                        }
                      >
                        {subStatus}
                      </Badge>
                    ) : null}
                    {!sub?.platformStripeConfigured ? (
                      <span className="text-xs text-muted-foreground">
                        Platform Stripe is not configured yet (SuperAdmin → Integrations).
                      </span>
                    ) : null}
                  </div>
                  <div className="flex flex-wrap items-end gap-3">
                    <div className="min-w-[180px]">
                      <Label>Upgrade / change plan</Label>
                      <Select value={upgradePlan} onValueChange={setUpgradePlan}>
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {PLAN_OPTIONS.map((p) => (
                            <SelectItem key={p.code} value={p.code}>
                              {p.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <Button
                      disabled={billingBusy || !sub?.platformStripeConfigured}
                      onClick={startPlanCheckout}
                    >
                      {billingBusy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                      Pay with Stripe Checkout
                    </Button>
                    {sub?.subscription?.stripe_customer_id ? (
                      <Button
                        variant="outline"
                        disabled={billingBusy}
                        onClick={openBillingPortal}
                      >
                        Manage billing
                      </Button>
                    ) : null}
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Guest payment gateway</CardTitle>
                  <CardDescription>
                    Your own Stripe keys for charging guest folios at{" "}
                    <span className="font-medium">/app/hotel/settings/payments</span>. Secrets
                    stay on this property — use test keys until go-live.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="grid gap-4 sm:grid-cols-2">
                    <div className="sm:col-span-2">
                      <Label>Gateway</Label>
                      <Select
                        value={payments.payment_gateway}
                        onValueChange={(payment_gateway) =>
                          setPayments((p) => ({ ...p, payment_gateway }))
                        }
                      >
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="none">Not configured</SelectItem>
                          <SelectItem value="stripe">Stripe</SelectItem>
                          <SelectItem value="adyen">Adyen</SelectItem>
                          <SelectItem value="manual">Manual / offline only</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="sm:col-span-2">
                      <Label>Publishable key</Label>
                      <Input
                        value={payments.payment_publishable_key}
                        onChange={(e) =>
                          setPayments((p) => ({
                            ...p,
                            payment_publishable_key: e.target.value,
                          }))
                        }
                        placeholder="pk_live_… or pk_test_…"
                      />
                    </div>
                    <div className="sm:col-span-2">
                      <Label>Secret key</Label>
                      <Input
                        type="password"
                        value={payments.payment_secret_key}
                        onChange={(e) =>
                          setPayments((p) => ({
                            ...p,
                            payment_secret_key: e.target.value,
                          }))
                        }
                        placeholder="sk_live_… or sk_test_…"
                      />
                    </div>
                    <div className="sm:col-span-2">
                      <Label>Webhook secret</Label>
                      <Input
                        type="password"
                        value={payments.payment_webhook_secret}
                        onChange={(e) =>
                          setPayments((p) => ({
                            ...p,
                            payment_webhook_secret: e.target.value,
                          }))
                        }
                        placeholder="whsec_…"
                      />
                    </div>
                    <div>
                      <Label>Deposit nights (guarantee)</Label>
                      <Input
                        type="number"
                        min={1}
                        max={14}
                        value={payments.deposit_nights}
                        onChange={(e) =>
                          setPayments((p) => ({
                            ...p,
                            deposit_nights: e.target.value,
                          }))
                        }
                      />
                      <p className="mt-1 text-xs text-muted-foreground">
                        Default card deposit = nightly rate × nights (Billing → Capture deposit).
                      </p>
                    </div>
                    <div>
                      <Label>HK turnaround days</Label>
                      <Input
                        type="number"
                        min={1}
                        max={14}
                        value={payments.turnaround_days}
                        onChange={(e) =>
                          setPayments((p) => ({
                            ...p,
                            turnaround_days: e.target.value,
                          }))
                        }
                      />
                      <p className="mt-1 text-xs text-muted-foreground">
                        After checkout, room stays dirty until available_from = today + this many days.
                      </p>
                    </div>
                  </div>
                  <Button
                    disabled={saving || !hotel}
                    onClick={() => saveHotel(payments)}
                  >
                    {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                    Save payment settings
                  </Button>
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="notifications" className="mt-4">
              <HotelNotificationsPanel />
            </TabsContent>

            <TabsContent value="channels" className="mt-4">
              <ChannelManagerPanel />
            </TabsContent>
          </Tabs>
        )}
      </div>
    </div>
  );
}
