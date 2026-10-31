"use client";

import Link from "next/link";
import {
  BarChart3,
  BedDouble,
  BookOpen,
  CalendarCheck2,
  CircleDollarSign,
  ClipboardList,
  FileSpreadsheet,
  History,
  LogIn,
  LogOut,
  Scale,
  Users,
  Percent,
  Radio,
  TrendingUp,
  Wallet,
  XCircle,
  Zap,
} from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { Separator } from "@/components/ui/separator";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";
import { HotelOpsHeader } from "@/components/hotel/hotel-ops-header";
import { useAuth } from "@/lib/auth";
import { useHotelPermissions } from "@/lib/rbac";

type ReportSection = "Operations" | "Revenue" | "Finance" | "Accounting";

type ReportCard = {
  href: string;
  title: string;
  description: string;
  icon: React.ElementType;
  section: ReportSection;
  show: boolean;
};

export default function HotelReportsHubPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);

  const cards: ReportCard[] = [
    {
      href: "/app/hotel/analytics",
      title: "Performance analytics",
      description: "Occupancy, ADR, RevPAR, trends, booking sources, and branch comparison. Excel/PDF export.",
      icon: BarChart3,
      section: "Operations",
      show: perms.canGenerateReports,
    },
    {
      href: "/app/hotel/reports/arrivals",
      title: "Arrival list",
      description: "Expected check-ins for a date range — guest, room, pax, and status. Excel/PDF export.",
      icon: LogIn,
      section: "Operations",
      show: perms.canGenerateReports,
    },
    {
      href: "/app/hotel/reports/departures",
      title: "Departure list",
      description: "Scheduled check-outs, still in-house vs already departed. Excel/PDF export.",
      icon: LogOut,
      section: "Operations",
      show: perms.canGenerateReports,
    },
    {
      href: "/app/hotel/reports/in-house",
      title: "In-house list",
      description: "Guests staying on a business date with room, stay dates, and pax. Excel/PDF export.",
      icon: Users,
      section: "Operations",
      show: perms.canGenerateReports,
    },
    {
      href: "/app/hotel/reports/check-ins",
      title: "Check-in / check-out log",
      description: "Combined arrivals and departures for a date range. Excel/PDF export.",
      icon: ClipboardList,
      section: "Operations",
      show: perms.canGenerateReports,
    },
    {
      href: "/app/hotel/reports/bookings",
      title: "Bookings & cancellations",
      description: "Stays overlapping the period, including cancelled and no-show counts. Excel/PDF export.",
      icon: CalendarCheck2,
      section: "Operations",
      show: perms.canGenerateReports,
    },
    {
      href: "/app/hotel/reports/rooms",
      title: "Rooms status",
      description: "Vacant / dirty / OOO ops view with filters and floor grouping. Excel/PDF export.",
      icon: BedDouble,
      section: "Operations",
      show: perms.canGenerateReports,
    },
    {
      href: "/app/hotel/reports/cashier-shift",
      title: "Cashier / shift report",
      description: "Posted payments for a shift window with totals by payment method. Excel/PDF export.",
      icon: Wallet,
      section: "Operations",
      show: perms.canGenerateReports || perms.canSeeFolios,
    },
    {
      href: "/app/hotel/reports/manager-flash",
      title: "Manager flash",
      description: "Daily occupancy, ADR, RevPAR, and room revenue with prior-day compare. Excel/PDF export.",
      icon: Zap,
      section: "Operations",
      show: perms.canGenerateReports || perms.canSeeFolios,
    },
    {
      href: "/app/hotel/reports/expected-occupancy",
      title: "Expected occupancy",
      description: "Forward 7–14 day sold / vacant / occupancy outlook with arrivals and departures. Excel/PDF export.",
      icon: BarChart3,
      section: "Operations",
      show: perms.canGenerateReports,
    },
    {
      href: "/app/hotel/reports/channel-sync",
      title: "Channel sync / booking import log",
      description:
        "Ops sync health plus audit trail of OTA bookings mapped to PMS reservations. Excel/PDF export.",
      icon: Radio,
      section: "Operations",
      show: perms.canGenerateReports || perms.canManageHotel,
    },
    {
      href: "/app/hotel/reports/channel-production",
      title: "Channel production",
      description: "Bookings, room nights, revenue, and ADR by OTA vs direct. Excel/PDF export.",
      icon: BarChart3,
      section: "Revenue",
      show: perms.canGenerateReports,
    },
    {
      href: "/app/hotel/reports/channel-commission",
      title: "Commission estimate",
      description: "OTA revenue × connection commission % with net after commission. Excel/PDF export.",
      icon: Percent,
      section: "Revenue",
      show: perms.canGenerateReports,
    },
    {
      href: "/app/hotel/reports/pickup-pace",
      title: "Pickup / pace",
      description: "On-the-books room nights for the next N days vs last year. Excel/PDF export.",
      icon: TrendingUp,
      section: "Revenue",
      show: perms.canGenerateReports,
    },
    {
      href: "/app/hotel/reports/forecast",
      title: "Forecast (30 / 90 days)",
      description: "Forward occupancy and projected room revenue / ADR / RevPAR. Excel/PDF export.",
      icon: TrendingUp,
      section: "Revenue",
      show: perms.canGenerateReports,
    },
    {
      href: "/app/hotel/reports/cancellations-by-source",
      title: "Cancellation & no-show by source",
      description: "Lost bookings and estimated lost revenue by channel. Excel/PDF export.",
      icon: XCircle,
      section: "Revenue",
      show: perms.canGenerateReports,
    },
    {
      href: "/app/hotel/revenue",
      title: "Revenue by booking",
      description: "Collected payments, charges, and outstanding balances by reservation. Excel/PDF export.",
      icon: CircleDollarSign,
      section: "Revenue",
      show: perms.canGenerateReports || perms.canSeeFolios,
    },
    {
      href: "/app/hotel/reports/profit-loss",
      title: "Profit & Loss",
      description: "Income, expenses, and net income for a date range. Excel/PDF export.",
      icon: Scale,
      section: "Finance",
      show: perms.canGenerateReports,
    },
    {
      href: "/app/hotel/reports/tax",
      title: "Tax summary",
      description: "Tax collected, paid, and net liability by tax account. Excel/PDF export.",
      icon: FileSpreadsheet,
      section: "Finance",
      show: perms.canGenerateReports,
    },
    {
      href: "/app/hotel/reports/invoices",
      title: "Invoices / AR",
      description: "Corporate city-ledger invoices with billed and outstanding totals. Excel/PDF export.",
      icon: BookOpen,
      section: "Finance",
      show: perms.canGenerateReports || perms.canSeeInvoices,
    },
    {
      href: "/app/hotel/reports/night-audit",
      title: "Night audit history",
      description: "Past closeouts with KPI summary, Excel/PDF list export, and per-run PDF.",
      icon: History,
      section: "Accounting",
      show: perms.canRunNightAudit,
    },
    {
      href: "/app/hotel/reports/ledger",
      title: "Trial balance / GL",
      description: "Period trial balance and general ledger detail. Excel/PDF export.",
      icon: BookOpen,
      section: "Accounting",
      show: perms.canSeeLedger || perms.canSeeTaxReports || perms.canManageHotel,
    },
  ];

  const sections = (
    ["Operations", "Revenue", "Finance", "Accounting"] as const
  ).map((section) => ({
    section,
    items: cards.filter((c) => c.section === section && c.show),
  }));

  const canSeeAny = cards.some((c) => c.show);

  return (
    <div className="flex flex-col">
      <header className="flex h-14 items-center gap-4 border-b bg-background px-6">
        <SidebarTrigger />
        <Separator orientation="vertical" className="h-6" />
        <h1 className="text-lg font-semibold">Reports</h1>
        <div className="ml-auto flex items-center gap-2">
          <ConnectionStatus />
          <ThemeToggle />
          <ChatToggleButton />
        </div>
      </header>

      <div className="flex-1 space-y-6 p-6">
        <HotelOpsHeader
          title="Reports"
          subtitle="Reports by access level — Accounting section is Admin / Accountant only"
          perms={perms}
        />

        {!canSeeAny ? (
          <p className="text-sm text-muted-foreground">
            No reports are available for your role.
          </p>
        ) : (
          <div className="space-y-8">
            {sections.map(({ section, items }) =>
              items.length === 0 ? null : (
                <section key={section} className="space-y-3">
                  <div className="flex items-center gap-2">
                    <ClipboardList className="h-4 w-4 text-muted-foreground" />
                    <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                      {section}
                    </h2>
                  </div>
                  <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                    {items.map((card) => {
                      const Icon = card.icon;
                      return (
                        <Link key={card.href} href={card.href} className="group">
                          <Card className="h-full transition-colors group-hover:border-foreground/30">
                            <CardHeader className="flex flex-row items-start gap-3 space-y-0">
                              <div className="rounded-md border bg-muted/40 p-2">
                                <Icon className="h-4 w-4" />
                              </div>
                              <div className="space-y-1">
                                <CardTitle className="text-base">{card.title}</CardTitle>
                                <CardDescription>{card.description}</CardDescription>
                              </div>
                            </CardHeader>
                            <CardContent>
                              <span className="text-xs font-medium text-muted-foreground group-hover:text-foreground">
                                Open report →
                              </span>
                            </CardContent>
                          </Card>
                        </Link>
                      );
                    })}
                  </div>
                </section>
              ),
            )}
          </div>
        )}
      </div>
    </div>
  );
}
