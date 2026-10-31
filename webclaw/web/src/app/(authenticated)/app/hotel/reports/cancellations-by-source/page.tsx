"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
import { getCancellationsBySource } from "@/lib/p1-reports-api";
import { formatMoney, hotelCurrency } from "@/lib/hospitality";
import type { ReportExportDoc } from "@/lib/report-export";
import { Loader2 } from "lucide-react";

function monthStart() {
  const d = new Date();
  return new Date(d.getFullYear(), d.getMonth(), 1).toISOString().slice(0, 10);
}

export default function CancellationsBySourceReportPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const canView = perms.canGenerateReports;
  const [fromDate, setFromDate] = useState(monthStart);
  const [toDate, setToDate] = useState(todayIso);

  const q = useQuery({
    queryKey: ["cancellations-by-source", fromDate, toDate],
    queryFn: () => getCancellationsBySource(fromDate, toDate),
    enabled: canView,
  });

  const currency = hotelCurrency(q.data?.hotel?.currency);
  const sources = useMemo(
    () => (Array.isArray(q.data?.sources) ? q.data.sources : []),
    [q.data],
  );
  const summary = q.data?.summary || {};
  const ready = canView && !!q.data && !q.isLoading && !q.isError;

  function buildExportDoc(): ReportExportDoc | null {
    if (!ready) return null;
    return {
      title: "Cancellation & no-show by source",
      subtitle: `${fromDate} → ${toDate}`,
      kpis: [
        { label: "Cancellations", value: summary.cancellations ?? 0 },
        { label: "No-shows", value: summary.no_shows ?? 0 },
        { label: "Lost revenue", value: formatMoney(summary.lost_revenue, currency) },
      ],
      sheets: [
        {
          name: "By source",
          headers: ["Source", "Bucket", "Cancels", "No-shows", "Lost nights", "Lost revenue"],
          rows: sources.map((s: Record<string, unknown>) => [
            s.label, s.bucket, s.cancellations, s.no_shows, s.lost_room_nights, s.lost_revenue,
          ]),
        },
      ],
    };
  }

  return (
    <ReportPageShell
      title="Cancellation & no-show by source"
      subtitle="Lost bookings and estimated lost revenue by OTA vs direct"
      actions={
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <Label className="text-xs text-muted-foreground">From</Label>
            <Input type="date" className="w-[150px]" value={fromDate} onChange={(e) => setFromDate(e.target.value)} />
          </div>
          <div>
            <Label className="text-xs text-muted-foreground">To</Label>
            <Input type="date" className="w-[150px]" value={toDate} onChange={(e) => setToDate(e.target.value)} />
          </div>
          <ReportExportButtons disabled={!ready} filename={`cancels-by-source-${fromDate}`} buildDoc={buildExportDoc} />
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
          <CardContent className="p-6 text-sm text-destructive">Could not load cancellations.</CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-3">
            {[
              { label: "Cancellations", value: String(summary.cancellations ?? 0) },
              { label: "No-shows", value: String(summary.no_shows ?? 0) },
              { label: "Lost revenue", value: formatMoney(summary.lost_revenue, currency) },
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
              <CardTitle className="text-base">By source</CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Source</TableHead>
                    <TableHead className="text-right">Cancels</TableHead>
                    <TableHead className="text-right">No-shows</TableHead>
                    <TableHead className="text-right">Lost nights</TableHead>
                    <TableHead className="text-right">Lost revenue</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {sources.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={5} className="text-muted-foreground">No cancellations or no-shows in range.</TableCell>
                    </TableRow>
                  ) : (
                    sources.map((s: Record<string, unknown>) => (
                      <TableRow key={String(s.label)}>
                        <TableCell className="font-medium">{String(s.label)}</TableCell>
                        <TableCell className="text-right tabular-nums">{String(s.cancellations)}</TableCell>
                        <TableCell className="text-right tabular-nums">{String(s.no_shows)}</TableCell>
                        <TableCell className="text-right tabular-nums">{String(s.lost_room_nights)}</TableCell>
                        <TableCell className="text-right tabular-nums">{formatMoney(s.lost_revenue, currency)}</TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </div>
      )}
    </ReportPageShell>
  );
}
