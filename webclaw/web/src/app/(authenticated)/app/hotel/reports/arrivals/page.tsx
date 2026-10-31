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

export default function ArrivalsReportPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const [fromDate, setFromDate] = useState(todayIso);
  const [toDate, setToDate] = useState(todayIso);

  const reservationsQ = useEntityList(
    SKILL,
    "list-reservations",
    { limit: "500", from_date: fromDate, to_date: toDate },
    { enabled: perms.canGenerateReports, refetchInterval: false },
  );

  const arrivals = useMemo(() => {
    const rows = itemsFrom<Reservation>(reservationsQ.data, "reservations");
    return rows
      .filter(
        (r) =>
          inRange(r.check_in, fromDate, toDate) &&
          !["cancelled", "no_show"].includes(r.status),
      )
      .sort(
        (a, b) =>
          a.check_in.localeCompare(b.check_in) ||
          guestName(a).localeCompare(guestName(b)),
      );
  }, [reservationsQ.data, fromDate, toDate]);

  const pending = arrivals.filter(
    (r) => r.status === "reserved" || r.status === "pre_checked_in",
  ).length;
  const arrived = arrivals.filter((r) => r.status === "checked_in" || r.status === "checked_out").length;
  const ready = perms.canGenerateReports && !reservationsQ.isLoading && !reservationsQ.isError;

  function buildExportDoc(): ReportExportDoc | null {
    if (!ready) return null;
    return {
      title: "Arrival list",
      subtitle: `${fromDate} to ${toDate}`,
      kpis: [
        { label: "Expected arrivals", value: arrivals.length },
        { label: "Still expected", value: pending },
        { label: "Already arrived", value: arrived },
      ],
      sheets: [
        {
          name: "Arrivals",
          headers: ["Arrival", "Guest", "Email", "Room", "Check-out", "Adults", "Children", "Status", "Notes"],
          rows: arrivals.map((r) => [
            r.check_in,
            guestName(r),
            r.guest_email || "",
            r.room_number || "",
            r.check_out,
            r.adults ?? "",
            r.children ?? "",
            reservationStatusLabel(r.status),
            r.notes || "",
          ]),
        },
      ],
    };
  }

  return (
    <ReportPageShell
      title="Arrival list"
      subtitle="Guests expected to check in for the selected dates"
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
            filename={`arrivals-${fromDate}-${toDate}`}
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
            Could not load arrivals
            {reservationsQ.error instanceof Error ? `: ${reservationsQ.error.message}` : "."}
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-3">
            <Kpi label="Expected arrivals" value={arrivals.length} />
            <Kpi label="Still expected" value={pending} />
            <Kpi label="Already arrived" value={arrived} />
          </div>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Arrivals</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Arrival</TableHead>
                    <TableHead>Guest</TableHead>
                    <TableHead>Room</TableHead>
                    <TableHead>Stay ends</TableHead>
                    <TableHead>Pax</TableHead>
                    <TableHead>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {arrivals.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={6} className="py-8 text-center text-muted-foreground">
                        No arrivals in this range.
                      </TableCell>
                    </TableRow>
                  ) : (
                    arrivals.map((r) => (
                      <TableRow key={r.id}>
                        <TableCell className="font-medium">{r.check_in}</TableCell>
                        <TableCell>
                          <div className="font-medium">{guestName(r)}</div>
                          <div className="text-xs text-muted-foreground">
                            {r.guest_email || "—"}
                          </div>
                        </TableCell>
                        <TableCell>{r.room_number || "Unassigned"}</TableCell>
                        <TableCell>{r.check_out}</TableCell>
                        <TableCell className="text-muted-foreground">
                          {(r.adults ?? 1) + (r.children ?? 0)}
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
