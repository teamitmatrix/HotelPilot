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
import { getExpectedOccupancy } from "@/lib/analytics-api";
import type { ReportExportDoc } from "@/lib/report-export";
import { Loader2 } from "lucide-react";

export default function ExpectedOccupancyReportPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const canView = perms.canGenerateReports;
  const [fromDate, setFromDate] = useState(todayIso);
  const [days, setDays] = useState("14");

  const occQ = useQuery({
    queryKey: ["expected-occupancy", fromDate, days],
    queryFn: () => getExpectedOccupancy(Number(days) || 14, fromDate),
    enabled: canView,
  });

  const data = occQ.data;
  const ready = canView && !!data && !occQ.isLoading && !occQ.isError;

  const summaryCards = useMemo(() => {
    if (!data?.summary) return [];
    return [
      { label: "Rooms available", value: String(data.summary.rooms_available) },
      { label: "Room nights sold", value: String(data.summary.room_nights_sold) },
      { label: "Avg occupancy", value: `${data.summary.avg_occupancy_pct}%` },
      { label: "Peak occupancy", value: `${data.summary.peak_occupancy_pct}%` },
    ];
  }, [data]);

  function buildExportDoc(): ReportExportDoc | null {
    if (!ready || !data) return null;
    return {
      title: "Expected occupancy",
      subtitle: `${data.period.from_date} → ${data.period.to_date} (${data.period.days} days)`,
      kpis: summaryCards,
      sheets: [
        {
          name: "Daily",
          headers: [
            "Date",
            "Sold",
            "Available",
            "Vacant",
            "Occupancy %",
            "Arrivals",
            "Departures",
          ],
          rows: (data.daily || []).map((row) => [
            row.date,
            row.rooms_sold,
            row.rooms_available,
            row.rooms_vacant,
            row.occupancy_pct,
            row.arrivals,
            row.departures,
          ]),
        },
      ],
    };
  }

  return (
    <ReportPageShell
      title="Expected occupancy"
      subtitle="Forward bookings — rooms sold, vacant, and occupancy % for the next 7–14 days"
      actions={
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <Label className="text-xs text-muted-foreground">From</Label>
            <Input
              type="date"
              className="w-[160px]"
              value={fromDate}
              onChange={(e) => setFromDate(e.target.value)}
            />
          </div>
          <div>
            <Label className="text-xs text-muted-foreground">Days</Label>
            <Select value={days} onValueChange={setDays}>
              <SelectTrigger className="w-[100px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="7">7</SelectItem>
                <SelectItem value="14">14</SelectItem>
                <SelectItem value="30">30</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <ReportExportButtons
            disabled={!ready}
            filename={`expected-occupancy-${fromDate}`}
            buildDoc={buildExportDoc}
          />
        </div>
      }
    >
      {!canView ? (
        <p className="text-sm text-muted-foreground">
          This report requires report generation access.
        </p>
      ) : occQ.isLoading ? (
        <div className="flex h-48 items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : occQ.isError ? (
        <Card>
          <CardContent className="p-6 text-sm text-destructive">
            Could not load expected occupancy
            {occQ.error instanceof Error ? `: ${occQ.error.message}` : "."}
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          <p className="text-sm text-muted-foreground">
            {data?.hotel?.hotel_name || "Property"} · {data?.period.from_date} →{" "}
            {data?.period.to_date}
          </p>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {summaryCards.map((card) => (
              <Card key={card.label}>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium text-muted-foreground">
                    {card.label}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-semibold tabular-nums">{card.value}</div>
                </CardContent>
              </Card>
            ))}
          </div>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Daily outlook</CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Date</TableHead>
                    <TableHead className="text-right">Sold</TableHead>
                    <TableHead className="text-right">Available</TableHead>
                    <TableHead className="text-right">Vacant</TableHead>
                    <TableHead className="text-right">Occupancy</TableHead>
                    <TableHead className="text-right">Arrivals</TableHead>
                    <TableHead className="text-right">Departures</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(data?.daily || []).map((row) => (
                    <TableRow key={row.date}>
                      <TableCell>{row.date}</TableCell>
                      <TableCell className="text-right tabular-nums">{row.rooms_sold}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {row.rooms_available}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{row.rooms_vacant}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {row.occupancy_pct}%
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{row.arrivals}</TableCell>
                      <TableCell className="text-right tabular-nums">{row.departures}</TableCell>
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
