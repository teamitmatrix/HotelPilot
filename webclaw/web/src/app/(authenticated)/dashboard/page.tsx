"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";
import {
  Zap,
  DollarSign,
  Users,
  Package,
  TrendingUp,
  Plus,
  Clock,
  ArrowRight,
  BarChart3,
  AlertCircle,
  FileText,
  Loader2,
  X,
  Database,
  Rocket,
  AlertTriangle,
  CalendarRange,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import {
  fetchApi,
  type Skill,
  skillDisplayName,
} from "@/lib/api";
import { useAuth, isHotelUser } from "@/lib/auth";
import { postLoginPath } from "@/lib/auth-routing";
import { useHotelPermissions } from "@/lib/rbac";
import { useChat } from "@/lib/chat";
import { ConnectionStatus } from "@/components/connection-status";
import { ExpansionPrompts } from "@/components/expansion-prompts";
import { AlertCards } from "@/components/alert-cards";
import { fetchUIConfig } from "@/lib/ui-config";
import type { UIConfig, KpiDef } from "@/lib/ui-yaml-types";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSkills } from "@/lib/hooks";
import {
  isPortfolioScope,
  useActivePropertyId,
} from "@/lib/active-tenant";
import { PortfolioDashboard } from "@/components/hotel/portfolio-dashboard";
import { addDays, isoDate } from "@/lib/hospitality";

// ── Greeting ────────────────────────────────────────────────────────────────

function getGreeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

/** Dashboard KPI lookback. ``to`` is exclusive (day after last included day). */
export type DashboardKpiRange = "today" | "7d" | "30d";

const KPI_RANGE_OPTIONS: { value: DashboardKpiRange; label: string }[] = [
  { value: "today", label: "Today" },
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
];

export function kpiRangeBounds(range: DashboardKpiRange, now = new Date()): {
  from: string;
  to: string;
} {
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const exclusiveEnd = addDays(start, 1);
  if (range === "today") {
    return { from: isoDate(start), to: isoDate(exclusiveEnd) };
  }
  if (range === "7d") {
    return { from: isoDate(addDays(start, -6)), to: isoDate(exclusiveEnd) };
  }
  return { from: isoDate(addDays(start, -29)), to: isoDate(exclusiveEnd) };
}

function firstName(fullName?: string): string {
  if (!fullName) return "";
  return fullName.split(" ")[0];
}

// ── Format helpers ──────────────────────────────────────────────────────────

function formatCurrency(val: unknown): string {
  const num = typeof val === "string" ? parseFloat(val) : Number(val);
  if (isNaN(num)) return String(val);
  if (Math.abs(num) >= 1_000_000) return `$${(num / 1_000_000).toFixed(1)}M`;
  if (Math.abs(num) >= 1_000) return `$${(num / 1_000).toFixed(1)}K`;
  return `$${num.toFixed(0)}`;
}

function formatNumber(val: unknown): string {
  const num = typeof val === "string" ? parseFloat(val) : Number(val);
  if (isNaN(num)) return String(val);
  if (num >= 1_000_000) return `${(num / 1_000_000).toFixed(1)}M`;
  if (num >= 1_000) return `${(num / 1_000).toFixed(1)}K`;
  return num.toLocaleString();
}

// ── Icon resolution ─────────────────────────────────────────────────────────

const ICON_MAP: Record<string, React.ReactNode> = {
  "trending-up": <TrendingUp className="h-4 w-4" />,
  "dollar-sign": <DollarSign className="h-4 w-4" />,
  "alert-circle": <AlertCircle className="h-4 w-4" />,
  clock: <Clock className="h-4 w-4" />,
  plus: <Plus className="h-4 w-4" />,
  users: <Users className="h-4 w-4" />,
  package: <Package className="h-4 w-4" />,
};

function resolveIcon(name?: string): React.ReactNode {
  return name && ICON_MAP[name] ? ICON_MAP[name] : <BarChart3 className="h-4 w-4" />;
}

// ── Headline KPI types ──────────────────────────────────────────────────────

interface HeadlineKpi {
  skill: string;
  skillLabel: string;
  label: string;
  value: string | number | null;
  subtitle?: string;
  type: "count" | "currency" | "percent";
  severity?: string;
  icon?: string;
  loading: boolean;
  href?: string;
  /** Hotel ops audience for permission filtering */
  audience?: "desk" | "rooms" | "finance";
}

// ── Quick action URL derivation ─────────────────────────────────────────────

function quickActionUrl(skill: string, action: string): string {
  if (skill === "hospitalityclaw") {
    if (action === "check-in" || action === "check-out" || action === "add-reservation") return "/app/hotel/desk";
    if (action === "add-room" || action === "add-room-type") return "/app/hotel/inventory";
    if (action === "add-guest") return "/app/hotel/desk";
  }
  if (action.startsWith("add-") || action.startsWith("create-")) {
    const entityPart = action.replace(/^(add|create)-/, "");
    const slug = entityPart.endsWith("y") && !entityPart.endsWith("ey") && !entityPart.endsWith("ay") && !entityPart.endsWith("oy")
      ? entityPart.slice(0, -1) + "ies"
      : entityPart.endsWith("s") || entityPart.endsWith("x") || entityPart.endsWith("ch") || entityPart.endsWith("sh")
        ? entityPart + "es"
        : entityPart + "s";
    return `/skills/${skill}/${slug}/new`;
  }
  if (action.startsWith("list-")) {
    const slug = action.replace(/^list-/, "");
    return `/skills/${skill}/${slug}`;
  }
  return `/skills/${skill}/actions?action=${action}`;
}

// ── Quick action discovery ──────────────────────────────────────────────────

interface QuickAction {
  skill: string;
  action: string;
  label: string;
}

function discoverQuickActions(skills: Skill[], hotelOps: boolean): QuickAction[] {
  const hotelPriority = [
    { skill: "hospitalityclaw", action: "add-reservation", label: "New Reservation" },
    { skill: "hospitalityclaw", action: "add-guest", label: "New Guest" },
    { skill: "hospitalityclaw", action: "add-room", label: "New Room" },
    { skill: "hospitalityclaw", action: "add-room-type", label: "New Room Type" },
    { skill: "hospitalityclaw", action: "check-in", label: "Check in" },
    { skill: "hospitalityclaw", action: "check-out", label: "Check out" },
  ];
  const priority = [
    { skill: "erpclaw", action: "add-customer", label: "New Customer" },
    { skill: "erpclaw", action: "add-sales-order", label: "New Sales Order" },
    { skill: "erpclaw", action: "add-sales-invoice", label: "New Invoice" },
    { skill: "erpclaw", action: "add-supplier", label: "New Supplier" },
    { skill: "erpclaw", action: "add-purchase-order", label: "New Purchase Order" },
    { skill: "erpclaw", action: "add-item", label: "New Item" },
    { skill: "erpclaw", action: "add-payment", label: "New Payment" },
    { skill: "erpclaw", action: "add-journal-entry", label: "New Journal Entry" },
    { skill: "erpclaw", action: "add-employee", label: "New Employee" },
    { skill: "erpclaw-growth", action: "add-lead", label: "New Lead" },
    { skill: "erpclaw-ops", action: "add-project", label: "New Project" },
    { skill: "erpclaw-ops", action: "add-issue", label: "New Issue" },
    { skill: "propertyclaw", action: "add-property", label: "New Property" },
    { skill: "propertyclaw", action: "add-work-order", label: "New Work Order" },
  ];

  const installed = new Set(skills.map((s) => s.name));
  const list = hotelOps ? hotelPriority : priority;
  return list.filter((a) => installed.has(a.skill)).slice(0, 8);
}

// ── KPI loading from UI.yaml configs ────────────────────────────────────────

async function loadHeadlineKpis(
  skills: Skill[],
  hotelOps: boolean,
  kpiRange: DashboardKpiRange = "today",
): Promise<HeadlineKpi[]> {
  const kpis: HeadlineKpi[] = [];

  if (hotelOps) {
    try {
      const { from, to } = kpiRangeBounds(kpiRange);
      const rangeQs = `from_date=${encodeURIComponent(from)}&to_date=${encodeURIComponent(to)}`;
      // Prefer status; finance roles may only have list-revenue.
      let status: Record<string, unknown> | null = null;
      try {
        const s = await fetchApi(`/hospitalityclaw/status?${rangeQs}`);
        if (s.status === "ok") status = s;
      } catch { /* try revenue below */ }

      if (!status) {
        try {
          const rev = await fetchApi(`/hospitalityclaw/list-revenue?limit=1&${rangeQs}`);
          if (rev.status === "ok") {
            status = {
              status: "ok",
              folios: null,
              revenue: rev.revenue_total ?? 0,
              charges_total: rev.charges_total ?? 0,
              outstanding_total: rev.outstanding_total ?? 0,
            };
            // Open folio count from revenue bookings when status is unavailable
            try {
              const folios = await fetchApi("/hospitalityclaw/list-folios?status=open&limit=0");
              if (folios.status === "ok") {
                status.folios = folios.total_count ?? 0;
              }
            } catch { /* optional */ }
            try {
              const reservations = await fetchApi(
                `/hospitalityclaw/list-reservations?limit=0&${rangeQs}`,
              );
              if (reservations.status === "ok") {
                status.reservations = reservations.total_count ?? 0;
              }
            } catch { /* optional */ }
          }
        } catch { /* fall through */ }
      }

      if (status) {
        const cards: {
          key: string;
          label: string;
          href: string;
          icon: string;
          type?: "count" | "currency";
          subtitle?: string;
          audience: "desk" | "rooms" | "finance";
        }[] = [
          { key: "reservations", label: "Reservations", href: "/app/hotel/desk", icon: "clock", audience: "desk" },
          { key: "guests", label: "Guests", href: "/app/hotel/desk", icon: "users", audience: "desk" },
          {
            key: "folios",
            label: "Open Folios",
            href: "/app/hotel/billing",
            icon: "dollar-sign",
            subtitle: "Unsettled guest accounts",
            audience: "finance",
          },
          {
            key: "charges_total",
            label: "Charges Posted",
            href: "/app/hotel/billing",
            icon: "dollar-sign",
            type: "currency",
            subtitle: "Room, tax & other charges",
            audience: "finance",
          },
          {
            key: "outstanding_total",
            label: "Outstanding",
            href: "/app/hotel/billing",
            icon: "alert-circle",
            type: "currency",
            subtitle: "Open folio balances",
            audience: "finance",
          },
          {
            key: "revenue",
            label: "Revenue",
            href: "/app/hotel/revenue",
            icon: "dollar-sign",
            type: "currency",
            subtitle: "Collected payments",
            audience: "finance",
          },
        ];
        for (const card of cards) {
          if (status[card.key] == null && card.key !== "folios") continue;
          const num = Number(status[card.key] ?? 0);
          kpis.push({
            skill: "hospitalityclaw",
            skillLabel: card.audience === "finance" ? "Billing" : "Front Desk",
            label: card.label,
            value: Number.isNaN(num) ? 0 : num,
            type: card.type ?? "count",
            icon: card.icon,
            loading: false,
            href: card.href,
            subtitle: card.subtitle,
            audience: card.audience,
          });
        }
        return kpis;
      }
    } catch { /* fall through to generic KPIs */ }
  }
  // Fetch UI configs + KPI values in parallel (only UI skills, cap at 12)
  const targetSkills = skills
    .filter((s) => s.has_ui && s.name !== "erpclaw-web" && s.name !== "webclaw")
    .slice(0, 12);

  const results = await Promise.all(
    targetSkills.map(async (skill): Promise<HeadlineKpi | null> => {
      // Try UI config first
      const config = await fetchUIConfig(skill.name);
      if (config?.dashboard?.kpis?.[0]) {
        const kpiDef = config.dashboard.kpis[0];
        try {
          const params = new URLSearchParams();
          params.set("limit", "0");
          if (kpiDef.filter) {
            for (const [k, v] of Object.entries(kpiDef.filter)) {
              params.set(k, String(v));
            }
          }
          const data = await fetchApi(`/${skill.name}/${kpiDef.action}?${params.toString()}`);
          const field = kpiDef.field || "total_count";
          const raw = data[field] ?? data.total_count ?? null;
          const value = raw !== null ? (typeof raw === "number" ? raw : Number(raw)) : null;

          let subtitle: string | undefined;
          if (kpiDef.total_action) {
            try {
              const totalData = await fetchApi(`/${skill.name}/${kpiDef.total_action}?limit=0`);
              const totalField = kpiDef.total_field || kpiDef.field || "total_count";
              const totalVal = totalData[totalField] ?? totalData.total_count;
              if (totalVal != null) {
                const prefix = kpiDef.type === "currency" ? "of $" : "of ";
                subtitle = `${prefix}${Number(totalVal).toLocaleString()}`;
              }
            } catch { /* ignore */ }
          }

          return {
            skill: skill.name,
            skillLabel: config.display_name || skillDisplayName(skill.name, skills),
            label: kpiDef.label,
            value,
            subtitle,
            type: kpiDef.type,
            severity: kpiDef.severity,
            icon: kpiDef.icon,
            loading: false,
          };
        } catch {
          return null;
        }
      }

      // Fallback: extract from status endpoint
      try {
        const status = await fetchApi(`/${skill.name}/status`);
        if (status.status !== "ok") return null;

        // Look for the first meaningful numeric value
        const SKIP = new Set([
          "status", "message", "version", "name", "skill", "description",
          "tier", "id", "port", "pid", "uptime", "code", "error", "actions",
        ]);
        for (const [key, val] of Object.entries(status)) {
          if (SKIP.has(key) || key.startsWith("_")) continue;
          if (typeof val === "string" && isNaN(Number(val))) continue;
          if (Array.isArray(val) || typeof val === "object") continue;
          const num = Number(val);
          if (!isNaN(num)) {
            const isCurrency = key.includes("amount") || key.includes("revenue") || key.includes("value") || key.includes("total");
            return {
              skill: skill.name,
              skillLabel: skillDisplayName(skill.name, skills),
              label: key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
              value: num,
              type: isCurrency ? "currency" : "count",
              loading: false,
            };
          }
        }
      } catch { /* ignore */ }

      return null;
    }),
  );

  for (const r of results) {
    if (r) kpis.push(r);
  }

  return kpis.slice(0, 8);
}

// ── Severity styling ────────────────────────────────────────────────────────

function severityBorderColor(severity?: string): string {
  switch (severity) {
    case "warning": return "kpi-card-warning";
    case "success": return "kpi-card-success";
    default: return "kpi-card";
  }
}

// ── Dashboard component ─────────────────────────────────────────────────────

export default function Dashboard() {
  const { user, loading } = useAuth();
  const router = useRouter();
  const dest = user ? postLoginPath(user) : "/dashboard";

  useEffect(() => {
    if (!loading && user && dest !== "/dashboard") {
      router.replace(dest);
    }
  }, [dest, loading, router, user]);

  if (loading || !user || dest !== "/dashboard") {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
      </div>
    );
  }
  return <OperatorDashboard />;
}

export function OperatorDashboard() {
  const { user } = useAuth();
  const hotelOps = isHotelUser(user);
  const hotelPerms = useHotelPermissions(user);
  const queryClient = useQueryClient();
  const { data: skills = [], isLoading: loading } = useSkills();
  const activePropertyId = useActivePropertyId();
  const portfolioMode =
    hotelOps &&
    isPortfolioScope(activePropertyId || user?.active_property_id);
  const [kpiRange, setKpiRange] = useState<DashboardKpiRange>("today");

  const { data: kpis = [], isLoading: kpisLoading } = useQuery({
    queryKey: [
      "headline-kpis",
      hotelOps,
      activePropertyId,
      kpiRange,
      skills.map((s) => s.name).sort(),
    ],
    queryFn: () => loadHeadlineKpis(skills, hotelOps, kpiRange),
    enabled: skills.length > 0 && !portfolioMode,
    staleTime: 60_000,
  });

  const [showOnboarding, setShowOnboarding] = useState(false);
  const [demoLoading, setDemoLoading] = useState(false);
  const { setPageContext } = useChat();

  useEffect(() => {
    setPageContext({ view: "dashboard" });
  }, [setPageContext]);

  // Show onboarding banner only when no data exists and not dismissed
  useEffect(() => {
    if (portfolioMode) return;
    if (localStorage.getItem("erpclaw_onboarding_dismissed")) {
      setShowOnboarding(false);
      return;
    }
    if (!kpisLoading && kpis.length > 0) {
      setShowOnboarding(false);
    } else if (!kpisLoading && kpis.length === 0) {
      setShowOnboarding(true);
    }
  }, [kpisLoading, kpis, portfolioMode]);

  function handleDismissOnboarding() {
    localStorage.setItem("erpclaw_onboarding_dismissed", "1");
    setShowOnboarding(false);
  }

  async function handleLoadDemoData() {
    setDemoLoading(true);
    try {
      const res = await fetchApi("/skills/erpclaw/execute", {
        method: "POST",
        body: JSON.stringify({ action: "seed-demo-data" }),
      });
      if (res.status === "ok") {
        localStorage.setItem("erpclaw_onboarding_dismissed", "1");
        setShowOnboarding(false);
        setDemoLoading(false);
        queryClient.invalidateQueries({ queryKey: ["headline-kpis"] });
      } else {
        setDemoLoading(false);
      }
    } catch {
      setDemoLoading(false);
    }
  }

  const quickActions = discoverQuickActions(skills, hotelOps).filter((qa) => {
    if (!hotelOps) return true;
    if (qa.action === "add-room" || qa.action === "add-room-type") return hotelPerms.canManageRooms;
    if (qa.action === "check-in") return hotelPerms.canCheckIn;
    if (qa.action === "check-out") return hotelPerms.canCheckOut;
    return hotelPerms.canWriteBookings || hotelPerms.canSeeFrontDesk;
  });

  const visibleKpis = hotelOps
    ? kpis.filter((kpi) => {
        if (kpi.audience === "finance" || kpi.label === "Folios" || kpi.label === "Open Folios"
          || kpi.label === "Revenue" || kpi.label === "Charges Posted" || kpi.label === "Outstanding") {
          return hotelPerms.canSeeFolios;
        }
        return hotelPerms.canSeeFrontDesk;
      })
    : kpis;

  const financeOnly = hotelOps && hotelPerms.canSeeFolios && !hotelPerms.canSeeFrontDesk;

  // Warning KPIs for "Needs Attention"
  const warningKpis = kpis.filter((k) => k.severity === "warning" && k.value !== null && Number(k.value) > 0);

  const customerKpi = kpis.find((k) => k.skill === "erpclaw" && k.label.toLowerCase().includes("customer"));
  const itemKpi = kpis.find((k) => k.skill === "erpclaw" && k.label.toLowerCase().includes("item"));

  const greeting = getGreeting();
  const name = firstName(user?.full_name);

  const subtitle = portfolioMode
    ? "Consolidated KPIs across every branch in your group"
    : financeOnly
      ? "Folios, charges, and outstanding balances for this property"
      : hotelOps
        ? "Guests, reservations, and billing for this property"
        : kpis.length > 0
          ? [
              customerKpi?.value != null ? `${customerKpi.value} customers` : null,
              itemKpi?.value != null ? `${itemKpi.value} items` : null,
            ]
              .filter(Boolean)
              .join(" · ") || "Your business at a glance"
          : skills.length > 0
            ? "Your business at a glance"
            : "Loading...";

  return (
    <div className="flex flex-col">
      <header className="flex h-14 items-center gap-4 border-b bg-background px-6">
        <SidebarTrigger />
        <Separator orientation="vertical" className="h-6" />
        <h1 className="text-lg font-semibold">
          {portfolioMode ? "Portfolio Dashboard" : "Dashboard"}
        </h1>
        <div className="ml-auto flex items-center gap-2">
          <ConnectionStatus />
          <ThemeToggle />
          <ChatToggleButton />
        </div>
      </header>

      <div className="flex-1 space-y-6 p-6">
        {/* Greeting */}
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-2xl font-bold tracking-tight">
              {greeting}{name ? `, ${name}` : ""}
            </h2>
            <p className="text-muted-foreground">{subtitle}</p>
          </div>
          {portfolioMode ? (
            <Badge variant="outline" className="gap-1">
              Consolidated
            </Badge>
          ) : hotelOps ? (
            <Select
              value={kpiRange}
              onValueChange={(v) => setKpiRange(v as DashboardKpiRange)}
            >
              <SelectTrigger
                className="w-[180px] gap-2"
                aria-label="KPI duration"
              >
                <CalendarRange className="h-4 w-4 shrink-0 text-muted-foreground" />
                <SelectValue placeholder="Select period" />
              </SelectTrigger>
              <SelectContent align="end">
                {KPI_RANGE_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <Badge variant="outline" className="gap-1">
              System Online
            </Badge>
          )}
        </div>

        {portfolioMode ? <PortfolioDashboard /> : null}

        {!portfolioMode && (
          <>
        {/* Onboarding Banner */}
        {showOnboarding && !hotelOps && (
          <div className="relative overflow-hidden rounded-xl bg-gradient-to-r from-blue-600 to-indigo-600 p-6 text-white shadow-lg">
            <button
              onClick={handleDismissOnboarding}
              className="absolute top-3 right-3 rounded-full p-1 text-white/70 hover:text-white hover:bg-white/10 transition-colors"
              aria-label="Dismiss"
            >
              <X className="h-4 w-4" />
            </button>
            <div className="flex flex-col sm:flex-row items-start sm:items-center gap-4">
              <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-white/15">
                <Rocket className="h-6 w-6" />
              </div>
              <div className="flex-1">
                <h3 className="text-lg font-semibold">Welcome to HotelPilot!</h3>
                <p className="text-sm text-white/80 mt-1">
                  Get started by loading demo data or setting up your first property.
                </p>
              </div>
              <div className="flex gap-2 shrink-0">
                <Button
                  onClick={handleLoadDemoData}
                  disabled={demoLoading}
                  className="bg-white text-blue-700 hover:bg-white/90 shadow-sm"
                >
                  {demoLoading ? (
                    <>
                      <Loader2 className="h-4 w-4 mr-1.5 animate-spin" />
                      Loading...
                    </>
                  ) : (
                    <>
                      <Database className="h-4 w-4 mr-1.5" />
                      Load Demo Data
                    </>
                  )}
                </Button>
                <Button
                  variant="outline"
                  onClick={handleDismissOnboarding}
                  className="bg-transparent border-white/30 text-white hover:bg-white/10 hover:text-white"
                >
                  Start Fresh
                </Button>
              </div>
            </div>
            <div className="absolute -top-6 -right-6 h-24 w-24 rounded-full bg-white/5" />
            <div className="absolute -bottom-4 -left-4 h-16 w-16 rounded-full bg-white/5" />
          </div>
        )}

        {/* Demo Data Loading Modal */}
        <Dialog open={demoLoading} onOpenChange={() => {}}>
          <DialogContent showCloseButton={false} className="sm:max-w-sm">
            <DialogHeader className="items-center text-center">
              <div className="mx-auto mb-2 flex h-12 w-12 items-center justify-center rounded-full bg-primary/10">
                <Loader2 className="h-6 w-6 animate-spin text-primary" />
              </div>
              <DialogTitle>Loading Demo Data</DialogTitle>
              <DialogDescription>
                Setting up sample data. This may take a moment...
              </DialogDescription>
            </DialogHeader>
          </DialogContent>
        </Dialog>

        {/* KPI Cards */}
        {kpisLoading ? (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Card key={i}>
                <CardHeader className="pb-2">
                  <div className="h-4 w-24 animate-pulse rounded bg-muted" />
                </CardHeader>
                <CardContent>
                  <div className="h-8 w-16 animate-pulse rounded bg-muted" />
                </CardContent>
              </Card>
            ))}
          </div>
        ) : visibleKpis.length > 0 ? (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            {visibleKpis.map((kpi, i) => (
              <Link key={i} href={kpi.href || `/skills/${kpi.skill}`}>
                <Card className={`${severityBorderColor(kpi.severity)} transition-colors hover:bg-slate-50 cursor-pointer h-full`}>
                  <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                    <CardTitle className="text-sm font-medium text-muted-foreground">
                      {kpi.label}
                    </CardTitle>
                    {resolveIcon(kpi.icon)}
                  </CardHeader>
                  <CardContent>
                    <div className="text-2xl font-bold">
                      {kpi.type === "currency" && kpi.value !== null
                        ? formatCurrency(kpi.value)
                        : kpi.value !== null
                          ? formatNumber(kpi.value)
                          : "\u2014"}
                    </div>
                    <p className="text-xs text-muted-foreground mt-1">
                      {kpi.subtitle || kpi.skillLabel}
                    </p>
                  </CardContent>
                </Card>
              </Link>
            ))}
          </div>
        ) : hotelOps ? (
          <Card>
            <CardContent className="p-6 text-sm text-muted-foreground">
              {financeOnly
                ? "Billing KPIs will appear here once folios and payments are posted."
                : "Property KPIs will appear here once hospitality data is available."}
            </CardContent>
          </Card>
        ) : (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            <Card>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">Business Modules</CardTitle>
                <Zap className="h-4 w-4 text-muted-foreground" />
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">{skills.filter((s) => s.has_ui).length}</div>
              </CardContent>
            </Card>
          </div>
        )}

        {/* Needs Attention */}
        {warningKpis.length > 0 && (
          <div className="space-y-3">
            <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-amber-500" />
              Needs Attention
            </h3>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {warningKpis.map((kpi, i) => (
                <Link key={i} href={kpi.href || `/skills/${kpi.skill}`}>
                  <Card className="kpi-card-warning transition-colors hover:bg-slate-50 cursor-pointer">
                    <CardContent className="pt-4 pb-3">
                      <div className="flex items-center justify-between">
                        <div>
                          <p className="text-sm font-medium">
                            {kpi.type === "currency" && kpi.value !== null
                              ? formatCurrency(kpi.value)
                              : formatNumber(kpi.value)} {kpi.label.toLowerCase()}
                          </p>
                          <p className="text-xs text-muted-foreground mt-0.5">{kpi.skillLabel}</p>
                        </div>
                        <ArrowRight className="h-4 w-4 text-muted-foreground" />
                      </div>
                    </CardContent>
                  </Card>
                </Link>
              ))}
            </div>
          </div>
        )}

        {!hotelOps && <AlertCards />}

        {!hotelOps && <ExpansionPrompts />}

        {/* Quick Actions */}
        {quickActions.length > 0 ? (
          <div className="space-y-4">
            <h3 className="text-lg font-semibold">Quick Actions</h3>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
              {quickActions.map((qa) => (
                <Link key={`${qa.skill}-${qa.action}`} href={quickActionUrl(qa.skill, qa.action)}>
                  <Button
                    variant="outline"
                    className="w-full justify-start gap-2 h-auto py-3"
                  >
                    <Plus className="h-4 w-4 text-primary" />
                    <span className="text-sm">{qa.label}</span>
                  </Button>
                </Link>
              ))}
            </div>
          </div>
        ) : null}
          </>
        )}

      </div>
    </div>
  );
}
