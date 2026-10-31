"use client";

import { useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ReportExportButtons, ReportPageShell } from "@/components/hotel/report-page-shell";
import { useAuth } from "@/lib/auth";
import { useHotelPermissions } from "@/lib/rbac";
import { useEntityList } from "@/lib/hooks";
import {
  SKILL,
  type HotelRoom,
  type RoomStatus,
  itemsFrom,
} from "@/lib/hospitality";
import type { ReportExportDoc } from "@/lib/report-export";
import { Loader2 } from "lucide-react";

const STATUS_ORDER: RoomStatus[] = ["vacant", "occupied", "dirty", "out_of_order"];
const FILTERS = ["all", "needs_attention", ...STATUS_ORDER] as const;

function statusLabel(status: string): string {
  if (status === "out_of_order") return "Out of order";
  if (status === "needs_attention") return "Needs attention";
  if (status === "all") return "All";
  return status.replace(/_/g, " ");
}

function attentionRank(status: string): number {
  if (status === "dirty") return 0;
  if (status === "out_of_order") return 1;
  if (status === "occupied") return 2;
  return 3;
}

export default function RoomsStatusReportPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("needs_attention");

  const roomsQ = useEntityList(
    SKILL,
    "list-rooms",
    { limit: "500" },
    { enabled: perms.canGenerateReports, refetchInterval: 15_000 },
  );

  const rooms = useMemo(() => {
    const list = itemsFrom<HotelRoom>(roomsQ.data, "rooms");
    return [...list].sort(
      (a, b) =>
        attentionRank(a.status) - attentionRank(b.status) ||
        String(a.floor || "").localeCompare(String(b.floor || ""), undefined, { numeric: true }) ||
        String(a.room_number).localeCompare(String(b.room_number), undefined, { numeric: true }),
    );
  }, [roomsQ.data]);

  const counts = useMemo(() => {
    const map: Record<string, number> = {
      vacant: 0,
      occupied: 0,
      dirty: 0,
      out_of_order: 0,
    };
    for (const room of rooms) {
      map[room.status] = (map[room.status] || 0) + 1;
    }
    return map;
  }, [rooms]);

  const filtered = useMemo(() => {
    if (filter === "all") return rooms;
    if (filter === "needs_attention") {
      return rooms.filter((r) => r.status === "dirty" || r.status === "out_of_order");
    }
    return rooms.filter((r) => r.status === filter);
  }, [rooms, filter]);

  const byFloor = useMemo(() => {
    const map = new Map<string, HotelRoom[]>();
    for (const room of filtered) {
      const key = room.floor || "Unassigned floor";
      const list = map.get(key) || [];
      list.push(room);
      map.set(key, list);
    }
    return [...map.entries()].sort((a, b) =>
      a[0].localeCompare(b[0], undefined, { numeric: true }),
    );
  }, [filtered]);

  const ready = perms.canGenerateReports && !roomsQ.isLoading && !roomsQ.isError;

  function buildExportDoc(): ReportExportDoc | null {
    if (!ready) return null;
    return {
      title: "Rooms status (ops)",
      subtitle: `Filter: ${statusLabel(filter)}`,
      kpis: [
        { label: "Total rooms", value: rooms.length },
        ...STATUS_ORDER.map((status) => ({
          label: statusLabel(status),
          value: counts[status] || 0,
        })),
        {
          label: "Needs attention",
          value: (counts.dirty || 0) + (counts.out_of_order || 0),
        },
      ],
      sheets: [
        {
          name: "Rooms",
          headers: ["Room", "Floor", "Type", "Status", "Available from"],
          rows: filtered.map((room) => [
            room.room_number,
            room.floor || "",
            room.room_type_name || room.room_type_code || "",
            statusLabel(room.status),
            room.available_from || "",
          ]),
        },
      ],
    };
  }

  return (
    <ReportPageShell
      title="Rooms status"
      subtitle="Vacant, dirty, occupied, and out-of-order rooms — ops view with filters"
      actions={
        <ReportExportButtons
          disabled={!ready}
          filename={`rooms-status-${filter}`}
          buildDoc={buildExportDoc}
        />
      }
    >
      {!perms.canGenerateReports ? (
        <p className="text-sm text-muted-foreground">
          This report requires Hotel Admin, General Manager, or Manager access.
        </p>
      ) : roomsQ.isLoading ? (
        <div className="flex h-48 items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : roomsQ.isError ? (
        <Card>
          <CardContent className="p-6 text-sm text-destructive">
            Could not load rooms
            {roomsQ.error instanceof Error ? `: ${roomsQ.error.message}` : "."}
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-5">
            <Kpi label="Total rooms" value={rooms.length} />
            {STATUS_ORDER.map((status) => (
              <Kpi key={status} label={statusLabel(status)} value={counts[status] || 0} />
            ))}
          </div>

          <div className="flex flex-wrap gap-2">
            {FILTERS.map((f) => (
              <Button
                key={f}
                size="sm"
                variant={filter === f ? "default" : "outline"}
                onClick={() => setFilter(f)}
              >
                {statusLabel(f)}
                {f === "needs_attention"
                  ? ` (${(counts.dirty || 0) + (counts.out_of_order || 0)})`
                  : f !== "all"
                    ? ` (${counts[f] || 0})`
                    : ` (${rooms.length})`}
              </Button>
            ))}
          </div>

          {byFloor.length === 0 ? (
            <Card>
              <CardContent className="py-8 text-center text-sm text-muted-foreground">
                No rooms match this filter.
              </CardContent>
            </Card>
          ) : (
            byFloor.map(([floor, floorRooms]) => (
              <Card key={floor}>
                <CardHeader>
                  <CardTitle className="text-base">
                    {floor}
                    <span className="ml-2 text-sm font-normal text-muted-foreground">
                      {floorRooms.length} room{floorRooms.length === 1 ? "" : "s"}
                    </span>
                  </CardTitle>
                </CardHeader>
                <CardContent className="p-0">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Room</TableHead>
                        <TableHead>Type</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead>Available from</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {floorRooms.map((room) => (
                        <TableRow key={room.id}>
                          <TableCell className="font-medium">{room.room_number}</TableCell>
                          <TableCell>
                            {room.room_type_name || room.room_type_code || "—"}
                          </TableCell>
                          <TableCell>
                            <Badge
                              variant={
                                room.status === "dirty" || room.status === "out_of_order"
                                  ? "destructive"
                                  : "secondary"
                              }
                            >
                              {statusLabel(room.status)}
                            </Badge>
                          </TableCell>
                          <TableCell className="text-muted-foreground">
                            {room.available_from || "—"}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            ))
          )}
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
