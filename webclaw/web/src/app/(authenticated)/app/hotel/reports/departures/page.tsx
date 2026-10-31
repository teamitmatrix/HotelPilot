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

export default function DeparturesReportPage() {
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

  const departures = useMemo(() => {
    const rows = itemsFrom<Reservation>(reservationsQ.data, "reservations");
    return rows
      .filter(
        (r) =>
          inRange(r.check_out, fromDate, toDate) &&
          !["cancelled", "no_show"].includes(r.status),
      )
      .sort(
        (a, b) =>
          a.check_out.localeCompare(b.check_out) ||
          guestName(a).localeCompare(guestName(b)),
      );
  }, [reservationsQ.data, fromDate, toDate]);

  const dueOut = departures.filter((r) => r.status === "checked_in").length;
  const checkedOut = departures.filter((r) => r.status === "checked_out").length;
  const ready = perms.canGenerateReports && !reservationsQ.isLoading && !reservationsQ.isError;

  function buildExportDoc(): ReportExportDoc | null {
    if (!ready) return null;
    return {
      title: "Departure list",
      subtitle: `${fromDate} to ${toDate}`,
      kpis: [
        { label: "Scheduled departures", value: departures.length },
        { label: "Still in-house", value: dueOut },
        { label: "Checked out", value: checkedOut },
      ],
      sheets: [
        {
          name: "Departures",
          headers: ["Departure", "Guest", "Email", "Room", "Check-in", "Status", "Notes"],
          rows: departures.map((r) => [
            r.check_out,
            guestName(r),
            r.guest_email || "",
            r.room_number || "",
            r.check_in,
            reservationStatusLabel(r.status),
            r.notes || "",
          ]),
        },
      ],
    };
  }

  return (
    <ReportPageShell
      title="Departure list"
      subtitle="Guests scheduled to check out for the selected dates"
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
            filename={`departures-${fromDate}-${toDate}`}
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
            Could not load departures
            {reservationsQ.error instanceof Error ? `: ${reservationsQ.error.message}` : "."}
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-3">
            <Kpi label="Scheduled departures" value={departures.length} />
            <Kpi label="Still in-house" value={dueOut} />
            <Kpi label="Checked out" value={checkedOut} />
          </div>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Departures</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Departure</TableHead>
                    <TableHead>Guest</TableHead>
                    <TableHead>Room</TableHead>
                    <TableHead>Arrived</TableHead>
                    <TableHead>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {departures.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={5} className="py-8 text-center text-muted-foreground">
                        No departures in this range.
                      </TableCell>
                    </TableRow>
                  ) : (
                    departures.map((r) => (
                      <TableRow key={r.id}>
                        <TableCell className="font-medium">{r.check_out}</TableCell>
                        <TableCell>
                          <div className="font-medium">{guestName(r)}</div>
                          <div className="text-xs text-muted-foreground">
                            {r.guest_email || "—"}
                          </div>
                        </TableCell>
                        <TableCell>{r.room_number || "—"}</TableCell>
                        <TableCell>{r.check_in}</TableCell>
                        <TableCell>
                          <Badge
                            variant={r.status === "checked_in" ? "default" : "secondary"}
                          >
                            {reservationStatusLabel(r.status)}
                          </Badge>
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
