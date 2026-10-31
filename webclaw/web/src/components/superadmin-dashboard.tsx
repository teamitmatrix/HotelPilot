"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  BedDouble,
  Building2,
  CalendarCheck,
  CheckCircle2,
  CreditCard,
  Database,
  Gauge,
  Loader2,
  Megaphone,
  Plus,
  RefreshCw,
  Server,
  ShieldAlert,
  UserPlus,
  Users,
  Zap,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { Textarea } from "@/components/ui/textarea";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";
import { useToast } from "@/components/toast-provider";
import {
  createAnnouncement,
  getPlatformAnalytics,
  type PlatformActivityEvent,
  type PlatformAnalytics,
} from "@/lib/admin-api";
import { useAuth } from "@/lib/auth";

function money(n: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(n || 0);
}

function formatNumber(n: number): string {
  return new Intl.NumberFormat("en-US").format(n || 0);
}

function relativeTime(iso: string): string {
  if (!iso) return "";
  const t = Date.parse(iso.includes("T") ? iso : iso.replace(" ", "T") + "Z");
  if (Number.isNaN(t)) return iso;
  const diff = Date.now() - t;
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "Just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 14) return `${days}d ago`;
  return new Date(t).toLocaleString();
}

function greeting(): string {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  return "Good evening";
}

function ActivityIcon({ kind }: { kind: PlatformActivityEvent["kind"] }) {
  switch (kind) {
    case "hotel":
      return <Building2 className="h-4 w-4 text-emerald-600" />;
    case "billing":
      return <CreditCard className="h-4 w-4 text-blue-600" />;
    case "security":
      return <ShieldAlert className="h-4 w-4 text-amber-600" />;
    default:
      return <Activity className="h-4 w-4 text-muted-foreground" />;
  }
}

function HealthBadge({ health }: { health: PlatformAnalytics["health"] | undefined }) {
  const operational = health?.status === "operational";
  return (
    <Badge
      variant="outline"
      className={`gap-1.5 px-2.5 py-1 ${
        operational
          ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
          : "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400"
      }`}
    >
      {operational ? (
        <CheckCircle2 className="h-3.5 w-3.5" />
      ) : (
        <AlertTriangle className="h-3.5 w-3.5" />
      )}
      {health?.label || "Checking systems…"}
    </Badge>
  );
}

export function SuperAdminGlobalDashboard() {
  const { user } = useAuth();
  const { showToast } = useToast();
  const [announceOpen, setAnnounceOpen] = useState(false);
  const [announceTitle, setAnnounceTitle] = useState("");
  const [announceBody, setAnnounceBody] = useState("");
  const [sending, setSending] = useState(false);

  const { data, isLoading, error, refetch, isFetching, dataUpdatedAt } = useQuery({
    queryKey: ["admin-analytics"],
    queryFn: getPlatformAnalytics,
    refetchInterval: 30_000,
  });

  useEffect(() => {
    document.title = "Dashboard — HotelPilot Central Admin";
  }, []);

  const first = user?.full_name?.split(" ")[0] || "";

  const kpiCards = useMemo(() => {
    const a = data;
    return [
      {
        key: "hotels",
        label: "Total Registered Hotels",
        value: formatNumber(a?.total_hotels ?? 0),
        hint: `${a?.active_hotels ?? 0} Active · ${a?.suspended_hotels ?? 0} Suspended · ${a?.onboarding_hotels ?? 0} Onboarding · ${a?.new_signups_30d ?? 0} new/30d`,
        icon: Building2,
        href: "/properties",
      },
      {
        key: "subs",
        label: "MRR / ARR",
        value: money(a?.subscription_mrr ?? 0),
        hint: `${a?.active_subscriptions ?? 0} active · ARR ${money(a?.subscription_arr ?? (a?.subscription_mrr ?? 0) * 12)} · churn ${a?.churn_30d ?? 0}/30d`,
        icon: CreditCard,
        href: "/subscriptions",
      },
      {
        key: "billing-risk",
        label: "Billing risk",
        value: formatNumber(a?.past_due_hotels ?? 0),
        hint: `${a?.failed_payments_open ?? 0} failed pays/30d · ${a?.trials_ending_7d ?? 0} trials ending`,
        icon: AlertTriangle,
        href: "/subscriptions",
      },
      {
        key: "rooms",
        label: "Platform Rooms & Bookings",
        value: formatNumber(a?.total_rooms ?? 0),
        hint: `${formatNumber(a?.active_bookings ?? 0)} active bookings · ${formatNumber(a?.booking_count ?? 0)} total`,
        icon: BedDouble,
        href: "/superadmin",
      },
      {
        key: "admins",
        label: "Active Hotel Admin Users",
        value: formatNumber(a?.active_admin_users ?? 0),
        hint: "Tenant owner & staff accounts online",
        icon: Users,
        href: "/settings?tab=users",
      },
    ];
  }, [data]);

  async function handleSendAnnouncement() {
    if (!announceTitle.trim()) return;
    setSending(true);
    try {
      await createAnnouncement({
        title: announceTitle.trim(),
        body: announceBody,
        channel: "both",
        audience: "all",
        status: "published",
      });
      showToast({
        type: "success",
        message: `Announcement “${announceTitle.trim()}” published to all hotel owners.`,
      });
      setAnnounceOpen(false);
      setAnnounceTitle("");
      setAnnounceBody("");
    } catch (e) {
      showToast({
        type: "error",
        message: (e as Error).message || "Failed to publish announcement",
      });
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="flex flex-col">
      <header className="flex h-14 items-center gap-4 border-b bg-background px-6">
        <SidebarTrigger />
        <Separator orientation="vertical" className="h-6" />
        <h1 className="text-lg font-semibold">Dashboard</h1>
        <div className="ml-auto flex items-center gap-2">
          <ConnectionStatus />
          <ThemeToggle />
          <ChatToggleButton />
        </div>
      </header>

      <div className="flex-1 space-y-6 p-6">
        {/* Hero */}
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h2 className="text-2xl font-bold tracking-tight">
              {greeting()}
              {first ? `, ${first}` : ""}
            </h2>
            <p className="text-muted-foreground">
              Global platform overview across every hotel tenant.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <HealthBadge health={data?.health} />
            <Button
              variant="outline"
              size="sm"
              onClick={() => refetch()}
              disabled={isFetching}
            >
              <RefreshCw className={`h-3.5 w-3.5 ${isFetching ? "animate-spin" : ""}`} />
              Refresh
            </Button>
          </div>
        </div>

        {error ? (
          <p className="text-sm text-destructive">{(error as Error).message}</p>
        ) : null}

        {/* KPI row */}
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {isLoading
            ? Array.from({ length: 4 }).map((_, i) => (
                <Card key={i}>
                  <CardHeader className="pb-2">
                    <div className="h-4 w-32 animate-pulse rounded bg-muted" />
                  </CardHeader>
                  <CardContent>
                    <div className="h-8 w-20 animate-pulse rounded bg-muted" />
                    <div className="mt-2 h-3 w-40 animate-pulse rounded bg-muted" />
                  </CardContent>
                </Card>
              ))
            : kpiCards.map((kpi) => {
                const Icon = kpi.icon;
                return (
                  <Link key={kpi.key} href={kpi.href}>
                    <Card className="kpi-card h-full transition-colors hover:bg-slate-50">
                      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                        <CardTitle className="text-sm font-medium text-muted-foreground">
                          {kpi.label}
                        </CardTitle>
                        <Icon className="h-4 w-4 text-muted-foreground" />
                      </CardHeader>
                      <CardContent>
                        <div className="text-2xl font-bold tracking-tight">{kpi.value}</div>
                        <p className="mt-1 text-xs text-muted-foreground">{kpi.hint}</p>
                      </CardContent>
                    </Card>
                  </Link>
                );
              })}
        </div>

        {/* Quick actions */}
        <div className="space-y-3">
          <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
            Quick Actions
          </h3>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            <Button asChild variant="outline" className="h-auto justify-start gap-2 py-3">
              <Link href="/properties?create=1">
                <Plus className="h-4 w-4 text-primary" />
                Register New Hotel
              </Link>
            </Button>
            <Button asChild variant="outline" className="h-auto justify-start gap-2 py-3">
              <Link href="/subscriptions?new=1">
                <CreditCard className="h-4 w-4 text-primary" />
                Add Subscription Plan
              </Link>
            </Button>
            <Button asChild variant="outline" className="h-auto justify-start gap-2 py-3">
              <Link href="/settings?tab=users&create=1">
                <UserPlus className="h-4 w-4 text-primary" />
                Create System User
              </Link>
            </Button>
            <Button
              variant="outline"
              className="h-auto justify-start gap-2 py-3"
              onClick={() => setAnnounceOpen(true)}
            >
              <Megaphone className="h-4 w-4 text-primary" />
              Send Platform Announcement
            </Button>
          </div>
        </div>

        <div className="grid gap-6 lg:grid-cols-3">
          {/* Activity log */}
          <Card className="lg:col-span-2">
            <CardHeader className="flex flex-row items-center justify-between pb-3">
              <div>
                <CardTitle className="text-base">Recent Platform Activity</CardTitle>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Live system events across hotels, billing, and security
                  {dataUpdatedAt
                    ? ` · updated ${relativeTime(new Date(dataUpdatedAt).toISOString())}`
                    : ""}
                </p>
              </div>
              <Button variant="ghost" size="sm" asChild>
                <Link href="/audit-logs">View all</Link>
              </Button>
            </CardHeader>
            <CardContent className="p-0">
              {isLoading ? (
                <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Loading activity…
                </div>
              ) : (data?.activity?.length ?? 0) > 0 ? (
                <ul className="divide-y">
                  {data!.activity.map((ev) => (
                    <li key={ev.id} className="flex items-start gap-3 px-6 py-3">
                      <ActivityIcon kind={ev.kind} />
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-medium">{ev.label}</p>
                        <p className="truncate text-xs text-muted-foreground">{ev.detail}</p>
                      </div>
                      <span className="shrink-0 text-[11px] text-muted-foreground">
                        {relativeTime(ev.created_at)}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="px-6 py-12 text-center text-sm text-muted-foreground">
                  No platform events yet. Register a hotel or process a payment to seed the feed.
                </div>
              )}
            </CardContent>
          </Card>

          {/* System health */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">System Health</CardTitle>
              <p className="text-xs text-muted-foreground mt-0.5">
                Infrastructure status for HotelPilot Central Admin
              </p>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-center justify-between rounded-lg border px-3 py-2.5">
                <div className="flex items-center gap-2 text-sm">
                  <Database className="h-4 w-4 text-muted-foreground" />
                  Database Connection
                </div>
                <Badge
                  variant="secondary"
                  className={
                    data?.health?.database === "ok"
                      ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400"
                      : "bg-amber-500/15 text-amber-700"
                  }
                >
                  {data?.health?.database === "ok" ? "Connected" : "Unavailable"}
                </Badge>
              </div>
              <div className="flex items-center justify-between rounded-lg border px-3 py-2.5">
                <div className="flex items-center gap-2 text-sm">
                  <Gauge className="h-4 w-4 text-muted-foreground" />
                  API Response Latency
                </div>
                <span className="text-sm font-medium tabular-nums">
                  {isLoading ? "—" : `${data?.health?.api_latency_ms ?? 0} ms`}
                </span>
              </div>
              <div className="flex items-center justify-between rounded-lg border px-3 py-2.5">
                <div className="flex items-center gap-2 text-sm">
                  <Server className="h-4 w-4 text-muted-foreground" />
                  Background Workers
                </div>
                <Badge
                  variant="secondary"
                  className={
                    data?.health?.workers === "ok"
                      ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400"
                      : "bg-amber-500/15 text-amber-700"
                  }
                >
                  {data?.health?.workers === "ok" ? "Running" : "Degraded"}
                </Badge>
              </div>
              <div
                className={`flex items-center gap-2 rounded-lg px-3 py-3 text-sm font-medium ${
                  data?.health?.status === "operational"
                    ? "bg-emerald-500/10 text-emerald-800 dark:text-emerald-300"
                    : "bg-amber-500/10 text-amber-800 dark:text-amber-300"
                }`}
              >
                {data?.health?.status === "operational" ? (
                  <Zap className="h-4 w-4" />
                ) : (
                  <AlertTriangle className="h-4 w-4" />
                )}
                {data?.health?.label || "Checking…"}
              </div>
            </CardContent>
          </Card>
        </div>
      </div>

      <Dialog open={announceOpen} onOpenChange={setAnnounceOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Send Platform Announcement</DialogTitle>
            <DialogDescription>
              Broadcast a message to all active hotel admin users. Manage drafts and
              tier targeting from{" "}
              <Link href="/announcements" className="underline underline-offset-2">
                Announcements
              </Link>
              .
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div className="space-y-1.5">
              <Label htmlFor="ann-title">Title</Label>
              <Input
                id="ann-title"
                value={announceTitle}
                onChange={(e) => setAnnounceTitle(e.target.value)}
                placeholder="Scheduled maintenance window"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="ann-body">Message</Label>
              <Textarea
                id="ann-body"
                value={announceBody}
                onChange={(e) => setAnnounceBody(e.target.value)}
                placeholder="Share details hotel operators should know…"
                rows={4}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAnnounceOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={handleSendAnnouncement}
              disabled={sending || !announceTitle.trim()}
            >
              {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Megaphone className="h-4 w-4" />}
              Send
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
