"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  ReportExportButtons,
  ReportPageShell,
  todayIso,
} from "@/components/hotel/report-page-shell";
import { useAuth } from "@/lib/auth";
import { useHotelPermissions } from "@/lib/rbac";
import { getForecast } from "@/lib/p1-reports-api";
import { formatMoney, hotelCurrency } from "@/lib/hospitality";
import type { ReportExportDoc } from "@/lib/report-export";
import { Loader2 } from "lucide-react";

export default function ForecastReportPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const canView = perms.canGenerateReports;
  const [fromDate, setFromDate] = useState(todayIso);
  const [days, setDays] = useState("30");

  const q = useQuery({
    queryKey: ["forecast", fromDate, days],
    queryFn: () => getForecast(Number(days) || 30, fromDate),
    enabled: canView,
  });

  const currency = hotelCurrency(q.data?.hotel?.currency);
  const daily = useMemo(
    () => (Array.isArray(q.data?.daily) ? q.data.daily : []),
    [q.data],
  );
  const summary = q.data?.summary || {};
  const ready = canView && !!q.data && !q.isLoading && !q.isError;

  function buildExportDoc(): ReportExportDoc | null {
    if (!ready) return null;
    return {
      title: "Forecast",
      subtitle: `Next ${days} days from ${fromDate}`,
      kpis: [
        { label: "Avg occupancy", value: `${summary.avg_occupancy_pct ?? 0}%` },
        { label: "Room nights sold", value: summary.room_nights_sold ?? 0 },
        { label: "Projected revenue", value: formatMoney(summary.projected_room_revenue, currency) },
        { label: "Projected ADR", value: formatMoney(summary.projected_adr, currency) },
      ],
      sheets: [
        {
          name: "Forecast",
          headers: ["Date", "Sold", "Available", "Occ %", "Revenue", "ADR", "RevPAR"],
          rows: daily.map((r: Record<string, unknown>) => [
            r.date, r.rooms_sold, r.rooms_available, r.occupancy_pct,
            r.projected_room_revenue, r.projected_adr, r.projected_revpar,
          ]),
        },
      ],
    };
  }

  return (
    <ReportPageShell
      title="Forecast"
      subtitle="On-books occupancy and projected room revenue for the next 30 / 90 days"
      actions={
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <Label className="text-xs text-muted-foreground">From</Label>
            <Input type="date" className="w-[150px]" value={fromDate} onChange={(e) => setFromDate(e.target.value)} />
          </div>
          <div>
            <Label className="text-xs text-muted-foreground">Days</Label>
            <Select value={days} onValueChange={setDays}>
              <SelectTrigger className="w-[100px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="30">30</SelectItem>
                <SelectItem value="90">90</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <ReportExportButtons disabled={!ready} filename={`forecast-${days}d-${fromDate}`} buildDoc={buildExportDoc} />
        </div>
      }
    >
      {!canView ? (
        <p className="text-sm text-muted-foreground">Report access required.</p>
      ) : q.isLoading ? (
        <div className="flex h-48 items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : q.isError ? (
        <Card>
          <CardContent className="p-6 text-sm text-destructive">Could not load forecast.</CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {[
              { label: "Avg occupancy", value: `${summary.avg_occupancy_pct ?? 0}%` },
              { label: "Room nights on books", value: String(summary.room_nights_sold ?? 0) },
              { label: "Projected revenue", value: formatMoney(summary.projected_room_revenue, currency) },
              { label: "Projected ADR", value: formatMoney(summary.projected_adr, currency) },
            ].map((c) => (
              <Card key={c.label}>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium text-muted-foreground">{c.label}</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-semibold tabular-nums">{c.value}</div>
                </CardContent>
              </Card>
            ))}
          </div>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Daily forecast</CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Date</TableHead>
                    <TableHead className="text-right">Sold</TableHead>
                    <TableHead className="text-right">Occ %</TableHead>
                    <TableHead className="text-right">Revenue</TableHead>
                    <TableHead className="text-right">ADR</TableHead>
                    <TableHead className="text-right">RevPAR</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {daily.map((r: Record<string, unknown>) => (
                    <TableRow key={String(r.date)}>
                      <TableCell>{String(r.date)}</TableCell>
                      <TableCell className="text-right tabular-nums">{String(r.rooms_sold)}</TableCell>
                      <TableCell className="text-right tabular-nums">{String(r.occupancy_pct)}%</TableCell>
                      <TableCell className="text-right tabular-nums">{formatMoney(r.projected_room_revenue, currency)}</TableCell>
                      <TableCell className="text-right tabular-nums">{formatMoney(r.projected_adr, currency)}</TableCell>
                      <TableCell className="text-right tabular-nums">{formatMoney(r.projected_revpar, currency)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </div>
      )}
    </ReportPageShell>
  );
}
