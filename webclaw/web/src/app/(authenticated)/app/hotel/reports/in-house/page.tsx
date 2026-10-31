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
  ReportExportButtons,
  ReportPageShell,
  todayIso,
} from "@/components/hotel/report-page-shell";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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

export default function InHouseReportPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const [asOf, setAsOf] = useState(todayIso);

  const reservationsQ = useEntityList(
    SKILL,
    "list-reservations",
    { limit: "500", from_date: asOf, to_date: asOf },
    { enabled: perms.canGenerateReports, refetchInterval: false },
  );

  const inHouse = useMemo(() => {
    const rows = itemsFrom<Reservation>(reservationsQ.data, "reservations");
    return rows
      .filter((r) => {
        if (r.status === "checked_in") return true;
        // Also include reserved stays that overlap the as-of night (expected in-house).
        if (["cancelled", "no_show", "checked_out"].includes(r.status)) return false;
        return r.check_in <= asOf && r.check_out > asOf;
      })
      .sort(
        (a, b) =>
          String(a.room_number || "").localeCompare(String(b.room_number || ""), undefined, {
            numeric: true,
          }) || guestName(a).localeCompare(guestName(b)),
      );
  }, [reservationsQ.data, asOf]);

  const checkedIn = inHouse.filter((r) => r.status === "checked_in").length;
  const dueOutToday = inHouse.filter((r) => r.check_out === asOf).length;
  const ready = perms.canGenerateReports && !reservationsQ.isLoading && !reservationsQ.isError;

  function buildExportDoc(): ReportExportDoc | null {
    if (!ready) return null;
    return {
      title: "In-house list",
      subtitle: `As of ${asOf}`,
      kpis: [
        { label: "In-house", value: inHouse.length },
        { label: "Checked in", value: checkedIn },
        { label: "Due out today", value: dueOutToday },
      ],
      sheets: [
        {
          name: "In-house",
          headers: [
            "Room",
            "Guest",
            "Email",
            "Check-in",
            "Check-out",
            "Adults",
            "Children",
            "Status",
            "Notes",
          ],
          rows: inHouse.map((r) => [
            r.room_number || "",
            guestName(r),
            r.guest_email || "",
            r.check_in,
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
      title="In-house list"
      subtitle="Guests staying on the selected business date"
      actions={
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <Label htmlFor="in-house-as-of" className="text-xs text-muted-foreground">
              As of
            </Label>
            <Input
              id="in-house-as-of"
              type="date"
              className="w-[160px]"
              value={asOf}
              onChange={(e) => setAsOf(e.target.value)}
            />
          </div>
          <ReportExportButtons
            disabled={!ready}
            filename={`in-house-${asOf}`}
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
            Could not load in-house list
            {reservationsQ.error instanceof Error ? `: ${reservationsQ.error.message}` : "."}
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-3">
            <Kpi label="In-house" value={inHouse.length} />
            <Kpi label="Checked in" value={checkedIn} />
            <Kpi label="Due out today" value={dueOutToday} />
          </div>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">In-house guests</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Room</TableHead>
                    <TableHead>Guest</TableHead>
                    <TableHead>Stay</TableHead>
                    <TableHead>Pax</TableHead>
                    <TableHead>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {inHouse.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={5} className="py-8 text-center text-muted-foreground">
                        No in-house guests for this date.
                      </TableCell>
                    </TableRow>
                  ) : (
                    inHouse.map((r) => (
                      <TableRow key={r.id}>
                        <TableCell className="font-medium">
                          {r.room_number || "Unassigned"}
                        </TableCell>
                        <TableCell>
                          <div className="font-medium">{guestName(r)}</div>
                          <div className="text-xs text-muted-foreground">
                            {r.guest_email || "—"}
                          </div>
                        </TableCell>
                        <TableCell className="text-muted-foreground">
                          {r.check_in} → {r.check_out}
                        </TableCell>
                        <TableCell>{(r.adults ?? 1) + (r.children ?? 0)}</TableCell>
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
