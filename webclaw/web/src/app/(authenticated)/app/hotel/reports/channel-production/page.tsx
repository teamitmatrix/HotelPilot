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
import { getChannelProduction } from "@/lib/p1-reports-api";
import { formatMoney, hotelCurrency } from "@/lib/hospitality";
import type { ReportExportDoc } from "@/lib/report-export";
import { Loader2 } from "lucide-react";

function monthStart() {
  const d = new Date();
  return new Date(d.getFullYear(), d.getMonth(), 1).toISOString().slice(0, 10);
}

export default function ChannelProductionReportPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const canView = perms.canGenerateReports;
  const [fromDate, setFromDate] = useState(monthStart);
  const [toDate, setToDate] = useState(todayIso);

  const q = useQuery({
    queryKey: ["channel-production", fromDate, toDate],
    queryFn: () => getChannelProduction(fromDate, toDate),
    enabled: canView,
  });

  const currency = hotelCurrency(q.data?.hotel?.currency);
  const channels = useMemo(
    () => (Array.isArray(q.data?.channels) ? q.data.channels : []),
    [q.data],
  );
  const summary = q.data?.summary || {};
  const ready = canView && !!q.data && !q.isLoading && !q.isError;

  function buildExportDoc(): ReportExportDoc | null {
    if (!ready) return null;
    return {
      title: "Channel production",
      subtitle: `${fromDate} → ${toDate}`,
      kpis: [
        { label: "Bookings", value: summary.bookings ?? 0 },
        { label: "Room nights", value: summary.room_nights ?? 0 },
        { label: "Revenue", value: formatMoney(summary.revenue, currency) },
        { label: "ADR", value: formatMoney(summary.adr, currency) },
      ],
      sheets: [
        {
          name: "Channels",
          headers: ["Channel", "Bucket", "Bookings", "Room nights", "Revenue", "ADR"],
          rows: channels.map((c: Record<string, unknown>) => [
            c.label,
            c.bucket,
            c.bookings,
            c.room_nights,
            c.revenue,
            c.adr,
          ]),
        },
      ],
    };
  }

  return (
    <ReportPageShell
      title="Channel production"
      subtitle="Bookings, revenue, and ADR by OTA vs direct"
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
          <ReportExportButtons disabled={!ready} filename={`channel-production-${fromDate}`} buildDoc={buildExportDoc} />
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
          <CardContent className="p-6 text-sm text-destructive">Could not load channel production.</CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {[
              { label: "Bookings", value: String(summary.bookings ?? 0) },
              { label: "Room nights", value: String(summary.room_nights ?? 0) },
              { label: "Revenue", value: formatMoney(summary.revenue, currency) },
              { label: "ADR", value: formatMoney(summary.adr, currency) },
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
              <CardTitle className="text-base">By channel</CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Channel</TableHead>
                    <TableHead>Bucket</TableHead>
                    <TableHead className="text-right">Bookings</TableHead>
                    <TableHead className="text-right">Room nights</TableHead>
                    <TableHead className="text-right">Revenue</TableHead>
                    <TableHead className="text-right">ADR</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {channels.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={6} className="text-muted-foreground">No production in range.</TableCell>
                    </TableRow>
                  ) : (
                    channels.map((c: Record<string, unknown>) => (
                      <TableRow key={String(c.label)}>
                        <TableCell className="font-medium">{String(c.label)}</TableCell>
                        <TableCell>{String(c.bucket)}</TableCell>
                        <TableCell className="text-right tabular-nums">{String(c.bookings)}</TableCell>
                        <TableCell className="text-right tabular-nums">{String(c.room_nights)}</TableCell>
                        <TableCell className="text-right tabular-nums">{formatMoney(c.revenue, currency)}</TableCell>
                        <TableCell className="text-right tabular-nums">{formatMoney(c.adr, currency)}</TableCell>
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
