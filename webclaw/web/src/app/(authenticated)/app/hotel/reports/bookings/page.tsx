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
  formatMoney,
  guestName,
  hotelCurrency,
  itemsFrom,
  reservationStatusLabel,
} from "@/lib/hospitality";
import type { ReportExportDoc } from "@/lib/report-export";
import { Loader2 } from "lucide-react";

export default function BookingsReportPage() {
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

  const reservations = useMemo(() => {
    const rows = itemsFrom<Reservation>(reservationsQ.data, "reservations");
    return [...rows].sort((a, b) => b.check_in.localeCompare(a.check_in));
  }, [reservationsQ.data]);

  const counts = useMemo(() => {
    const total = reservations.length;
    const cancelled = reservations.filter((r) => r.status === "cancelled").length;
    const noShow = reservations.filter((r) => r.status === "no_show").length;
    const active = reservations.filter(
      (r) => !["cancelled", "no_show", "checked_out"].includes(r.status),
    ).length;
    const checkedOut = reservations.filter((r) => r.status === "checked_out").length;
    return { total, cancelled, noShow, active, checkedOut };
  }, [reservations]);

  const currency = hotelCurrency(reservationsQ.data?.currency);
  const ready = perms.canGenerateReports && !reservationsQ.isLoading && !reservationsQ.isError;

  function buildExportDoc(): ReportExportDoc | null {
    if (!ready) return null;
    return {
      title: "Bookings & cancellations",
      subtitle: `${fromDate} to ${toDate}`,
      kpis: [
        { label: "Total stays", value: counts.total },
        { label: "Active / upcoming", value: counts.active },
        { label: "Checked out", value: counts.checkedOut },
        { label: "Cancelled", value: counts.cancelled },
        { label: "No-show", value: counts.noShow },
      ],
      sheets: [
        {
          name: "Bookings",
          headers: ["Guest", "Email", "Room", "Type", "Check-in", "Check-out", "Rate", "Status"],
          rows: reservations.map((r) => [
            guestName(r),
            r.guest_email || "",
            r.room_number || "",
            r.room_type_name || "",
            r.check_in,
            r.check_out,
            formatMoney(r.rate, currency),
            reservationStatusLabel(r.status),
          ]),
        },
      ],
    };
  }

  return (
    <ReportPageShell
      title="Bookings & cancellations"
      subtitle="Reservations overlapping the selected stay period"
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
            filename={`bookings-${fromDate}-${toDate}`}
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
            Could not load bookings
            {reservationsQ.error instanceof Error ? `: ${reservationsQ.error.message}` : "."}
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-5">
            <Kpi label="Total stays" value={counts.total} />
            <Kpi label="Active / upcoming" value={counts.active} />
            <Kpi label="Checked out" value={counts.checkedOut} />
            <Kpi label="Cancelled" value={counts.cancelled} />
            <Kpi label="No-show" value={counts.noShow} />
          </div>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Bookings</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Guest</TableHead>
                    <TableHead>Room</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>Check-in</TableHead>
                    <TableHead>Check-out</TableHead>
                    <TableHead>Rate</TableHead>
                    <TableHead>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {reservations.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={7} className="py-8 text-center text-muted-foreground">
                        No bookings in this range.
                      </TableCell>
                    </TableRow>
                  ) : (
                    reservations.map((r) => (
                      <TableRow key={r.id}>
                        <TableCell>
                          <div className="font-medium">{guestName(r)}</div>
                          <div className="text-xs text-muted-foreground">{r.guest_email || "—"}</div>
                        </TableCell>
                        <TableCell>{r.room_number || "—"}</TableCell>
                        <TableCell>{r.room_type_name || "—"}</TableCell>
                        <TableCell>{r.check_in}</TableCell>
                        <TableCell>{r.check_out}</TableCell>
                        <TableCell>{formatMoney(r.rate, currency)}</TableCell>
                        <TableCell>
                          <Badge
                            variant={
                              r.status === "cancelled" || r.status === "no_show"
                                ? "warning"
                                : "secondary"
                            }
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
