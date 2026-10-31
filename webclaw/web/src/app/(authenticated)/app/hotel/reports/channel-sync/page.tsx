"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  ReportExportButtons,
  ReportPageShell,
  todayIso,
} from "@/components/hotel/report-page-shell";
import { useAuth } from "@/lib/auth";
import { useHotelPermissions } from "@/lib/rbac";
import { fetchApi } from "@/lib/api";
import type { ReportExportDoc } from "@/lib/report-export";
import { Loader2 } from "lucide-react";

type SyncLogRow = {
  id: string;
  hotel_id?: string | null;
  ota?: string | null;
  ota_label?: string | null;
  direction?: string | null;
  kind?: string | null;
  status?: string | null;
  message?: string | null;
  created_at?: string | null;
  reservation_ids?: string[];
};

type StayRow = {
  reservation_id: string;
  status?: string | null;
  check_in?: string | null;
  check_out?: string | null;
  rate?: string | null;
  guest_name?: string | null;
  guest_email?: string | null;
  room_number?: string | null;
  room_type_name?: string | null;
  missing?: boolean;
};

type ImportRow = {
  id: string;
  hotel_id?: string | null;
  hotel_name?: string | null;
  ota?: string | null;
  ota_label?: string | null;
  external_id?: string | null;
  reservation_ids?: string[];
  stays?: StayRow[];
  created_at?: string | null;
};

function daysAgoIso(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

function formatWhen(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export default function ChannelSyncReportPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const canView = perms.canGenerateReports || perms.canManageHotel;
  const [fromDate, setFromDate] = useState(daysAgoIso(7));
  const [toDate, setToDate] = useState(todayIso);
  const [direction, setDirection] = useState("all");
  const [status, setStatus] = useState("all");
  const [kind, setKind] = useState("all");
  const [tab, setTab] = useState("ops");

  const reportQ = useQuery({
    queryKey: ["channel-sync-report", fromDate, toDate, direction, status, kind],
    queryFn: async () => {
      const params = new URLSearchParams();
      params.set("from_date", fromDate);
      params.set("to_date", toDate);
      params.set("limit", "500");
      params.set("include_audit", "true");
      if (direction !== "all") params.set("direction", direction);
      if (status !== "all") params.set("status", status);
      if (kind !== "all") params.set("kind", kind);
      return fetchApi(`/hotel/channels/sync-log?${params.toString()}`);
    },
    enabled: canView,
  });

  const logs = useMemo(
    () => (Array.isArray(reportQ.data?.logs) ? (reportQ.data.logs as SyncLogRow[]) : []),
    [reportQ.data],
  );
  const imports = useMemo(
    () =>
      Array.isArray(reportQ.data?.imports)
        ? (reportQ.data.imports as ImportRow[])
        : Array.isArray(reportQ.data?.audit?.imports)
          ? (reportQ.data.audit.imports as ImportRow[])
          : [],
    [reportQ.data],
  );
  const summary = (reportQ.data?.summary || {}) as {
    sync_events?: number;
    sync_errors?: number;
    booking_imports?: number;
    linked_reservations?: number;
    returned?: number;
    errors?: number;
  };
  const ready = canView && !reportQ.isLoading && !reportQ.isError;

  function buildExportDoc(): ReportExportDoc | null {
    if (!ready) return null;
    return {
      title: "Channel sync / booking import log",
      subtitle: `Ops + audit · ${fromDate} → ${toDate}`,
      kpis: [
        { label: "Sync events", value: summary.sync_events ?? summary.returned ?? logs.length },
        { label: "Sync errors", value: summary.sync_errors ?? summary.errors ?? 0 },
        { label: "Booking imports", value: summary.booking_imports ?? imports.length },
        { label: "Linked reservations", value: summary.linked_reservations ?? 0 },
      ],
      sheets: [
        {
          name: "Ops sync log",
          headers: [
            "When",
            "OTA",
            "Direction",
            "Kind",
            "Status",
            "Message",
            "Reservations",
          ],
          rows: logs.map((row) => [
            formatWhen(row.created_at),
            row.ota_label || row.ota || "",
            row.direction || "",
            row.kind || "",
            row.status || "",
            row.message || "",
            (row.reservation_ids || []).join(", "),
          ]),
        },
        {
          name: "Audit imports",
          headers: [
            "Imported at",
            "OTA",
            "External booking ID",
            "Hotel",
            "Reservation IDs",
            "Guest",
            "Room",
            "Stay",
            "Status",
          ],
          rows: imports.flatMap((row) => {
            const stays = row.stays?.length ? row.stays : [null];
            return stays.map((stay) => [
              formatWhen(row.created_at),
              row.ota_label || row.ota || "",
              row.external_id || "",
              row.hotel_name || row.hotel_id || "",
              (row.reservation_ids || []).join(", "),
              stay?.guest_name || stay?.guest_email || "",
              stay?.room_number || "",
              stay
                ? `${stay.check_in || ""} → ${stay.check_out || ""}`
                : "",
              stay?.status || "",
            ]);
          }),
        },
      ],
    };
  }

  return (
    <ReportPageShell
      title="Channel sync / booking import log"
      subtitle="Ops sync health and audit trail of OTA bookings mapped into PMS reservations"
      actions={
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <Label className="text-xs text-muted-foreground">From</Label>
            <Input
              type="date"
              className="w-[150px]"
              value={fromDate}
              onChange={(e) => setFromDate(e.target.value)}
            />
          </div>
          <div>
            <Label className="text-xs text-muted-foreground">To</Label>
            <Input
              type="date"
              className="w-[150px]"
              value={toDate}
              onChange={(e) => setToDate(e.target.value)}
            />
          </div>
          <div>
            <Label className="text-xs text-muted-foreground">Direction</Label>
            <Select value={direction} onValueChange={setDirection}>
              <SelectTrigger className="w-[120px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All</SelectItem>
                <SelectItem value="outbound">Outbound</SelectItem>
                <SelectItem value="inbound">Inbound</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className="text-xs text-muted-foreground">Status</Label>
            <Select value={status} onValueChange={setStatus}>
              <SelectTrigger className="w-[120px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All</SelectItem>
                <SelectItem value="success">Success</SelectItem>
                <SelectItem value="error">Error</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className="text-xs text-muted-foreground">Kind</Label>
            <Select value={kind} onValueChange={setKind}>
              <SelectTrigger className="w-[140px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All</SelectItem>
                <SelectItem value="booking">Booking import</SelectItem>
                <SelectItem value="availability">Availability</SelectItem>
                <SelectItem value="rates">Rates</SelectItem>
                <SelectItem value="mapping">Mapping</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <ReportExportButtons
            disabled={!ready}
            filename={`channel-sync-audit-${fromDate}-${toDate}`}
            buildDoc={buildExportDoc}
          />
        </div>
      }
    >
      {!canView ? (
        <p className="text-sm text-muted-foreground">
          This report requires Manager or report access.
        </p>
      ) : reportQ.isLoading ? (
        <div className="flex h-48 items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : reportQ.isError ? (
        <Card>
          <CardContent className="p-6 text-sm text-destructive">
            Could not load channel sync / import log
            {reportQ.error instanceof Error ? `: ${reportQ.error.message}` : "."}
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground">
                  Sync events
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-semibold">
                  {summary.sync_events ?? logs.length}
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground">
                  Sync errors
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-semibold">
                  {summary.sync_errors ?? 0}
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground">
                  Booking imports
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-semibold">
                  {summary.booking_imports ?? imports.length}
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground">
                  Linked reservations
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-semibold">
                  {summary.linked_reservations ?? 0}
                </div>
              </CardContent>
            </Card>
          </div>

          <Tabs value={tab} onValueChange={setTab}>
            <TabsList>
              <TabsTrigger value="ops">Ops — sync log</TabsTrigger>
              <TabsTrigger value="audit">Audit — booking imports</TabsTrigger>
            </TabsList>

            <TabsContent value="ops" className="mt-4">
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">
                    Outbound pushes &amp; inbound events
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>When</TableHead>
                        <TableHead>OTA</TableHead>
                        <TableHead>Direction</TableHead>
                        <TableHead>Kind</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead>Message</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {logs.length === 0 ? (
                        <TableRow>
                          <TableCell colSpan={6} className="text-muted-foreground">
                            No sync events in this range.
                          </TableCell>
                        </TableRow>
                      ) : (
                        logs.map((row) => (
                          <TableRow key={row.id}>
                            <TableCell className="whitespace-nowrap text-sm">
                              {formatWhen(row.created_at)}
                            </TableCell>
                            <TableCell>{row.ota_label || row.ota || "—"}</TableCell>
                            <TableCell>{row.direction || "—"}</TableCell>
                            <TableCell>{row.kind || "—"}</TableCell>
                            <TableCell>
                              <Badge
                                variant={
                                  row.status === "error" ? "destructive" : "secondary"
                                }
                              >
                                {row.status || "—"}
                              </Badge>
                            </TableCell>
                            <TableCell className="max-w-md text-sm text-muted-foreground">
                              {row.message || "—"}
                              {(row.reservation_ids || []).length > 0 ? (
                                <span className="mt-1 block text-xs">
                                  Reservations: {(row.reservation_ids || []).join(", ")}
                                </span>
                              ) : null}
                            </TableCell>
                          </TableRow>
                        ))
                      )}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="audit" className="mt-4">
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">
                    OTA booking → PMS reservation mapping
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Imported</TableHead>
                        <TableHead>OTA</TableHead>
                        <TableHead>External ID</TableHead>
                        <TableHead>Guest / room</TableHead>
                        <TableHead>Stay</TableHead>
                        <TableHead>Reservation</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {imports.length === 0 ? (
                        <TableRow>
                          <TableCell colSpan={6} className="text-muted-foreground">
                            No booking imports recorded in this range.
                          </TableCell>
                        </TableRow>
                      ) : (
                        imports.map((row) => {
                          const stay = row.stays?.[0];
                          return (
                            <TableRow key={row.id}>
                              <TableCell className="whitespace-nowrap text-sm">
                                {formatWhen(row.created_at)}
                              </TableCell>
                              <TableCell>{row.ota_label || row.ota || "—"}</TableCell>
                              <TableCell className="font-mono text-xs">
                                {row.external_id || "—"}
                              </TableCell>
                              <TableCell className="text-sm">
                                <div>{stay?.guest_name || stay?.guest_email || "—"}</div>
                                <div className="text-xs text-muted-foreground">
                                  {stay?.room_number
                                    ? `Room ${stay.room_number}`
                                    : stay?.room_type_name || row.hotel_name || ""}
                                </div>
                              </TableCell>
                              <TableCell className="text-sm tabular-nums">
                                {stay?.check_in && stay?.check_out
                                  ? `${stay.check_in} → ${stay.check_out}`
                                  : "—"}
                                {stay?.status ? (
                                  <Badge variant="outline" className="ml-2">
                                    {stay.status}
                                  </Badge>
                                ) : null}
                              </TableCell>
                              <TableCell className="text-xs">
                                {(row.reservation_ids || []).length === 0 ? (
                                  "—"
                                ) : (
                                  <div className="flex flex-col gap-1">
                                    {(row.reservation_ids || []).map((rid) => (
                                      <Link
                                        key={rid}
                                        href="/app/hotel/desk"
                                        className="font-mono text-primary hover:underline"
                                        title={rid}
                                      >
                                        {rid.slice(0, 8)}…
                                      </Link>
                                    ))}
                                  </div>
                                )}
                              </TableCell>
                            </TableRow>
                          );
                        })
                      )}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            </TabsContent>
          </Tabs>
        </div>
      )}
    </ReportPageShell>
  );
}
