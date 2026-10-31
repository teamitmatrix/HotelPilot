"use client";

import { useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  ReportDateRange,
  ReportExportButtons,
  ReportPageShell,
  monthStartIso,
  todayIso,
} from "@/components/hotel/report-page-shell";
import { useAuth } from "@/lib/auth";
import { useHotelPermissions } from "@/lib/rbac";
import { useEntityList } from "@/lib/hooks";
import {
  SKILL,
  type Reservation,
  guestName,
  itemsFrom,
  reservationStatusLabel,
} from "@/lib/hospitality";
import type { ReportExportDoc } from "@/lib/report-export";
import { Loader2 } from "lucide-react";

function inRange(day: string, from: string, to: string): boolean {
  return day >= from && day <= to;
}

export default function CheckInsReportPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const [fromDate, setFromDate] = useState(monthStartIso);
  const [toDate, setToDate] = useState(todayIso);

  const reservationsQ = useEntityList(
    SKILL,
    "list-reservations",
    { limit: "500", from_date: fromDate, to_date: toDate },
    { enabled: perms.canGenerateReports, refetchInterval: false },
  );

  const reservations = itemsFrom<Reservation>(reservationsQ.data, "reservations");

  const arrivals = useMemo(
    () =>
      reservations
        .filter((r) => inRange(r.check_in, fromDate, toDate) && r.status !== "cancelled")
        .sort((a, b) => a.check_in.localeCompare(b.check_in) || guestName(a).localeCompare(guestName(b))),
    [reservations, fromDate, toDate],
  );

  const departures = useMemo(
    () =>
      reservations
        .filter((r) => inRange(r.check_out, fromDate, toDate) && r.status !== "cancelled")
        .sort((a, b) => a.check_out.localeCompare(b.check_out) || guestName(a).localeCompare(guestName(b))),
    [reservations, fromDate, toDate],
  );

  const checkedInCount = arrivals.filter((r) => r.status === "checked_in" || r.status === "checked_out").length;
  const checkedOutCount = departures.filter((r) => r.status === "checked_out").length;
  const ready = perms.canGenerateReports && !reservationsQ.isLoading && !reservationsQ.isError;

  function buildExportDoc(): ReportExportDoc | null {
    if (!ready) return null;
    const row = (r: Reservation, dateKey: "check_in" | "check_out") => [
      r[dateKey],
      guestName(r),
      r.guest_email || "",
      r.room_number || "",
      `${r.check_in} → ${r.check_out}`,
      reservationStatusLabel(r.status),
    ];
    return {
      title: "Check-in / check-out log",
      subtitle: `${fromDate} to ${toDate}`,
      kpis: [
        { label: "Scheduled arrivals", value: arrivals.length },
        { label: "Arrived / in-house", value: checkedInCount },
        { label: "Scheduled departures", value: departures.length },
        { label: "Checked out", value: checkedOutCount },
      ],
      sheets: [
        {
          name: "Arrivals",
          headers: ["Date", "Guest", "Email", "Room", "Stay", "Status"],
          rows: arrivals.map((r) => row(r, "check_in")),
        },
        {
          name: "Departures",
          headers: ["Date", "Guest", "Email", "Room", "Stay", "Status"],
          rows: departures.map((r) => row(r, "check_out")),
        },
      ],
    };
  }

  return (
    <ReportPageShell
      title="Check-in / check-out log"
      subtitle="Arrivals and departures for the selected dates"
      actions={
        <div className="flex flex-wrap items-end gap-3">
          <ReportDateRange
            fromDate={fromDate}
            toDate={toDate}
            onFromChange={setFromDate}
            onToChange={setToDate}
          />
          <ReportExportButtons
            disabled={!ready}
            filename={`check-ins-${fromDate}-${toDate}`}
            buildDoc={buildExportDoc}
          />
        </div>
      }
    >
      {!perms.canGenerateReports ? (
        <p className="text-sm text-muted-foreground">
          This report requires Hotel Admin, General Manager, or Manager access.
        </p>
      ) : reservationsQ.isLoading ? (
        <div className="flex h-48 items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : reservationsQ.isError ? (
        <Card>
          <CardContent className="p-6 text-sm text-destructive">
            Could not load reservations
            {reservationsQ.error instanceof Error ? `: ${reservationsQ.error.message}` : "."}
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-4">
            <Kpi label="Scheduled arrivals" value={arrivals.length} />
            <Kpi label="Arrived / in-house" value={checkedInCount} />
            <Kpi label="Scheduled departures" value={departures.length} />
            <Kpi label="Checked out" value={checkedOutCount} />
          </div>

          <ActivityTable title="Arrivals (check-in date)" rows={arrivals} dateKey="check_in" />
          <ActivityTable title="Departures (check-out date)" rows={departures} dateKey="check_out" />
        </div>
      )}
    </ReportPageShell>
  );
}

function Kpi({ label, value }: { label: string; value: number }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{label}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-semibold">{value}</div>
      </CardContent>
    </Card>
  );
}

function ActivityTable({
  title,
  rows,
  dateKey,
}: {
  title: string;
  rows: Reservation[];
  dateKey: "check_in" | "check_out";
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Date</TableHead>
              <TableHead>Guest</TableHead>
              <TableHead>Room</TableHead>
              <TableHead>Stay</TableHead>
              <TableHead>Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5} className="py-8 text-center text-muted-foreground">
                  No rows in this range.
                </TableCell>
              </TableRow>
            ) : (
              rows.map((r) => (
                <TableRow key={`${dateKey}-${r.id}`}>
                  <TableCell>{r[dateKey]}</TableCell>
                  <TableCell>
                    <div className="font-medium">{guestName(r)}</div>
                    <div className="text-xs text-muted-foreground">{r.guest_email || "—"}</div>
                  </TableCell>
                  <TableCell>{r.room_number || "—"}</TableCell>
                  <TableCell className="text-muted-foreground">
                    {r.check_in} → {r.check_out}
                  </TableCell>
                  <TableCell>
                    <Badge variant="secondary">{reservationStatusLabel(r.status)}</Badge>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
