"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  ReportExportButtons,
  ReportPageShell,
  todayIso,
} from "@/components/hotel/report-page-shell";
import { useAuth } from "@/lib/auth";
import { useHotelPermissions } from "@/lib/rbac";
import { getPerformanceAnalytics } from "@/lib/analytics-api";
import { formatMoney, hotelCurrency } from "@/lib/hospitality";
import type { ReportExportDoc } from "@/lib/report-export";
import { Loader2 } from "lucide-react";

function priorDay(iso: string): string {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() - 1);
  return d.toISOString().slice(0, 10);
}

export default function ManagerFlashReportPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const canView = perms.canGenerateReports || perms.canSeeFolios;
  const [businessDate, setBusinessDate] = useState(todayIso);
  const previousDate = priorDay(businessDate);

  const flashQ = useQuery({
    queryKey: ["manager-flash", businessDate],
    queryFn: () => getPerformanceAnalytics(businessDate, businessDate),
    enabled: canView,
  });

  const priorQ = useQuery({
    queryKey: ["manager-flash-prior", previousDate],
    queryFn: () => getPerformanceAnalytics(previousDate, previousDate),
    enabled: canView,
  });

  const data = flashQ.data;
  const prior = priorQ.data;
  const currency = hotelCurrency(data?.hotel?.currency);
  const kpis = data?.kpis;
  const ready = canView && !!data && !flashQ.isLoading && !flashQ.isError;

  const cards = useMemo(() => {
    if (!kpis) return [];
    return [
      {
        label: "Room revenue",
        value: formatMoney(kpis.room_revenue, currency),
        prior: prior?.kpis ? formatMoney(prior.kpis.room_revenue, currency) : null,
      },
      {
        label: "Occupancy",
        value: `${kpis.occupancy_pct}%`,
        prior: prior?.kpis ? `${prior.kpis.occupancy_pct}%` : null,
      },
      {
        label: "ADR",
        value: formatMoney(kpis.adr, currency),
        prior: prior?.kpis ? formatMoney(prior.kpis.adr, currency) : null,
      },
      {
        label: "RevPAR",
        value: formatMoney(kpis.revpar, currency),
        prior: prior?.kpis ? formatMoney(prior.kpis.revpar, currency) : null,
      },
      {
        label: "Rooms sold",
        value: String(kpis.rooms_sold),
        prior: prior?.kpis ? String(prior.kpis.rooms_sold) : null,
      },
      {
        label: "Cancellations",
        value: String(kpis.cancellations),
        prior: prior?.kpis ? String(prior.kpis.cancellations) : null,
      },
    ];
  }, [kpis, prior, currency]);

  function buildExportDoc(): ReportExportDoc | null {
    if (!ready || !kpis) return null;
    return {
      title: "Manager flash",
      subtitle: `Business date ${businessDate}`,
      kpis: cards.map((c) => ({ label: c.label, value: c.value })),
      sheets: [
        {
          name: "Flash",
          headers: ["Metric", "Today", "Prior day"],
          rows: cards.map((c) => [c.label, c.value, c.prior || ""]),
        },
        {
          name: "Sources",
          headers: ["Source", "Bookings", "Revenue", "Share %"],
          rows: (data?.booking_sources?.sources || []).map((s) => [
            s.label,
            s.bookings,
            s.revenue,
            s.share_pct,
          ]),
        },
      ],
    };
  }

  return (
    <ReportPageShell
      title="Manager flash"
      subtitle="Daily snapshot — occupancy, ADR, RevPAR, and room revenue"
      actions={
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <Label className="text-xs text-muted-foreground">Business date</Label>
            <Input
              type="date"
              className="w-[160px]"
              value={businessDate}
              onChange={(e) => setBusinessDate(e.target.value)}
            />
          </div>
          <ReportExportButtons
            disabled={!ready}
            filename={`manager-flash-${businessDate}`}
            buildDoc={buildExportDoc}
          />
        </div>
      }
    >
      {!canView ? (
        <p className="text-sm text-muted-foreground">
          This report requires Manager or billing access.
        </p>
      ) : flashQ.isLoading ? (
        <div className="flex h-48 items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : flashQ.isError ? (
        <Card>
          <CardContent className="p-6 text-sm text-destructive">
            Could not load flash report
            {flashQ.error instanceof Error ? `: ${flashQ.error.message}` : "."}
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          <p className="text-sm text-muted-foreground">
            {data?.hotel?.hotel_name || "Property"} · compared with {previousDate}
          </p>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {cards.map((card) => (
              <Card key={card.label}>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium text-muted-foreground">
                    {card.label}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-semibold tabular-nums">{card.value}</div>
                  {card.prior ? (
                    <p className="mt-1 text-xs text-muted-foreground">
                      Prior day: {card.prior}
                    </p>
                  ) : null}
                </CardContent>
              </Card>
            ))}
          </div>

          {(data?.booking_sources?.sources || []).length > 0 ? (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Booking sources</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                {(data?.booking_sources?.sources || []).map((s) => (
                  <div key={s.key} className="flex justify-between gap-3 border-b py-2 last:border-0">
                    <span>{s.label}</span>
                    <span className="tabular-nums text-muted-foreground">
                      {s.bookings} · {formatMoney(s.revenue, currency)} · {s.share_pct}%
                    </span>
                  </div>
                ))}
              </CardContent>
            </Card>
          ) : null}
        </div>
      )}
    </ReportPageShell>
  );
}
