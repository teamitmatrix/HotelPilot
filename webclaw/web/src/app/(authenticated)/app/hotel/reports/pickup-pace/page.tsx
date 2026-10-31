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
import { getPickupPace } from "@/lib/p1-reports-api";
import type { ReportExportDoc } from "@/lib/report-export";
import { Loader2 } from "lucide-react";

export default function PickupPaceReportPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const canView = perms.canGenerateReports;
  const [asOf, setAsOf] = useState(todayIso);
  const [days, setDays] = useState("30");

  const q = useQuery({
    queryKey: ["pickup-pace", asOf, days],
    queryFn: () => getPickupPace(asOf, Number(days) || 30),
    enabled: canView,
  });

  const daily = useMemo(
    () => (Array.isArray(q.data?.daily) ? q.data.daily : []),
    [q.data],
  );
  const summary = q.data?.summary || {};
  const ready = canView && !!q.data && !q.isLoading && !q.isError;

  function buildExportDoc(): ReportExportDoc | null {
    if (!ready) return null;
    return {
      title: "Pickup / pace",
      subtitle: `As of ${asOf} · ${days} days · TY vs LY`,
      kpis: [
        { label: "TY room nights", value: summary.ty_room_nights ?? 0 },
        { label: "LY room nights", value: summary.ly_room_nights ?? 0 },
        { label: "Variance", value: summary.variance ?? 0 },
        { label: "Pace %", value: `${summary.pace_pct ?? 0}%` },
      ],
      sheets: [
        {
          name: "Pace",
          headers: ["Offset", "TY date", "TY sold", "LY date", "LY sold", "Variance"],
          rows: daily.map((r: Record<string, unknown>) => [
            r.offset, r.ty_date, r.ty_rooms_sold, r.ly_date, r.ly_rooms_sold, r.variance,
          ]),
        },
      ],
    };
  }

  return (
    <ReportPageShell
      title="Pickup / pace"
      subtitle="On-the-books room nights for the next N days vs the same window last year"
      actions={
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <Label className="text-xs text-muted-foreground">As of</Label>
            <Input type="date" className="w-[150px]" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
          </div>
          <div>
            <Label className="text-xs text-muted-foreground">Horizon</Label>
            <Select value={days} onValueChange={setDays}>
              <SelectTrigger className="w-[100px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="14">14</SelectItem>
                <SelectItem value="30">30</SelectItem>
                <SelectItem value="60">60</SelectItem>
                <SelectItem value="90">90</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <ReportExportButtons disabled={!ready} filename={`pickup-pace-${asOf}`} buildDoc={buildExportDoc} />
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
          <CardContent className="p-6 text-sm text-destructive">Could not load pickup pace.</CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {[
              { label: "TY room nights", value: String(summary.ty_room_nights ?? 0) },
              { label: "LY room nights", value: String(summary.ly_room_nights ?? 0) },
              { label: "Variance", value: String(summary.variance ?? 0) },
              { label: "Pace vs LY", value: `${summary.pace_pct ?? 0}%` },
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
              <CardTitle className="text-base">Daily TY vs LY</CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>TY date</TableHead>
                    <TableHead className="text-right">TY sold</TableHead>
                    <TableHead>LY date</TableHead>
                    <TableHead className="text-right">LY sold</TableHead>
                    <TableHead className="text-right">Variance</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {daily.map((r: Record<string, unknown>) => (
                    <TableRow key={String(r.ty_date)}>
                      <TableCell>{String(r.ty_date)}</TableCell>
                      <TableCell className="text-right tabular-nums">{String(r.ty_rooms_sold)}</TableCell>
                      <TableCell>{String(r.ly_date)}</TableCell>
                      <TableCell className="text-right tabular-nums">{String(r.ly_rooms_sold)}</TableCell>
                      <TableCell className="text-right tabular-nums">{String(r.variance)}</TableCell>
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
