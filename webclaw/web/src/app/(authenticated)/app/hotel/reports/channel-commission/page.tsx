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
import { getChannelCommission } from "@/lib/p1-reports-api";
import { formatMoney, hotelCurrency } from "@/lib/hospitality";
import type { ReportExportDoc } from "@/lib/report-export";
import { Loader2 } from "lucide-react";

function monthStart() {
  const d = new Date();
  return new Date(d.getFullYear(), d.getMonth(), 1).toISOString().slice(0, 10);
}

export default function ChannelCommissionReportPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const canView = perms.canGenerateReports;
  const [fromDate, setFromDate] = useState(monthStart);
  const [toDate, setToDate] = useState(todayIso);

  const q = useQuery({
    queryKey: ["channel-commission", fromDate, toDate],
    queryFn: () => getChannelCommission(fromDate, toDate),
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
      title: "Commission estimate by channel",
      subtitle: `${fromDate} → ${toDate}`,
      kpis: [
        { label: "OTA revenue", value: formatMoney(summary.ota_revenue, currency) },
        { label: "Commission", value: formatMoney(summary.commission_estimate, currency) },
        { label: "Net", value: formatMoney(summary.net_revenue, currency) },
      ],
      sheets: [
        {
          name: "Commission",
          headers: ["Channel", "Revenue", "Comm %", "Commission", "Net"],
          rows: channels.map((c: Record<string, unknown>) => [
            c.label, c.revenue, c.commission_percent, c.commission_estimate, c.net_revenue,
          ]),
        },
      ],
    };
  }

  return (
    <ReportPageShell
      title="Commission estimate"
      subtitle="OTA revenue × connection commission % (set under Channel settings)"
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
          <ReportExportButtons disabled={!ready} filename={`channel-commission-${fromDate}`} buildDoc={buildExportDoc} />
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
          <CardContent className="p-6 text-sm text-destructive">Could not load commission estimate.</CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-3">
            {[
              { label: "OTA revenue", value: formatMoney(summary.ota_revenue, currency) },
              { label: "Commission est.", value: formatMoney(summary.commission_estimate, currency) },
              { label: "Net after commission", value: formatMoney(summary.net_revenue, currency) },
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
              <CardTitle className="text-base">By OTA</CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Channel</TableHead>
                    <TableHead className="text-right">Revenue</TableHead>
                    <TableHead className="text-right">Comm %</TableHead>
                    <TableHead className="text-right">Commission</TableHead>
                    <TableHead className="text-right">Net</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {channels.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={5} className="text-muted-foreground">
                        No OTA production — set commission % on channel connections.
                      </TableCell>
                    </TableRow>
                  ) : (
                    channels.map((c: Record<string, unknown>) => (
                      <TableRow key={String(c.label)}>
                        <TableCell className="font-medium">{String(c.label)}</TableCell>
                        <TableCell className="text-right tabular-nums">{formatMoney(c.revenue, currency)}</TableCell>
                        <TableCell className="text-right tabular-nums">{String(c.commission_percent)}%</TableCell>
                        <TableCell className="text-right tabular-nums">{formatMoney(c.commission_estimate, currency)}</TableCell>
                        <TableCell className="text-right tabular-nums">{formatMoney(c.net_revenue, currency)}</TableCell>
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
