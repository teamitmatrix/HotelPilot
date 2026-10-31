"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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
import { SidebarTrigger } from "@/components/ui/sidebar";
import { Separator } from "@/components/ui/separator";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";
import { HotelOpsHeader } from "@/components/hotel/hotel-ops-header";
import { useAuth } from "@/lib/auth";
import { useHotelPermissions } from "@/lib/rbac";
import { useEntityList } from "@/lib/hooks";
import { useToast } from "@/components/toast-provider";
import { SKILL, type Hotel, type RoomType, itemsFrom } from "@/lib/hospitality";
import {
  fetchChannelAllotments,
  saveChannelAllotments,
} from "@/lib/channel-api";
import { ArrowLeft, Loader2 } from "lucide-react";

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}
function plusDays(iso: string, days: number) {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

export default function ChannelAllotmentsPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const canEdit = perms.canManageHotel || perms.canManageRooms;

  const hotelsQ = useEntityList(SKILL, "list-hotels", { limit: "20" }, { enabled: canEdit });
  const typesQ = useEntityList(SKILL, "list-room-types", { limit: "200" }, { enabled: canEdit });
  const hotels = itemsFrom<Hotel>(hotelsQ.data, "hotels");
  const types = itemsFrom<RoomType>(typesQ.data, "room_types");
  const hotelId = hotels[0]?.id ?? "";

  const [ota, setOta] = useState("booking_com");
  const [roomTypeId, setRoomTypeId] = useState("");
  const [startDate, setStartDate] = useState(todayIso);
  const [endDate, setEndDate] = useState(() => plusDays(todayIso(), 13));
  const [allotment, setAllotment] = useState("5");
  const [busy, setBusy] = useState(false);

  const listQ = useQuery({
    queryKey: ["channel-allotments", hotelId, startDate, endDate, ota, roomTypeId],
    queryFn: () =>
      fetchChannelAllotments({
        hotel_id: hotelId,
        start_date: startDate,
        end_date: endDate >= startDate ? endDate : startDate,
        ota,
        room_type_id: roomTypeId || undefined,
      }),
    enabled: canEdit && !!hotelId,
  });

  async function onSave() {
    if (!hotelId || !roomTypeId) {
      showToast({ type: "error", message: "Select a room type" });
      return;
    }
    setBusy(true);
    try {
      await saveChannelAllotments({
        hotel_id: hotelId,
        ota,
        room_type_id: roomTypeId,
        start_date: startDate,
        end_date: endDate,
        allotment: Number(allotment) || 0,
      });
      showToast({ type: "success", message: "Allotment saved and sync queued" });
      await queryClient.invalidateQueries({ queryKey: ["channel-allotments"] });
    } catch (err) {
      showToast({
        type: "error",
        message: err instanceof Error ? err.message : "Could not save allotment",
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col">
      <header className="flex h-14 items-center gap-4 border-b bg-background px-6">
        <SidebarTrigger />
        <Separator orientation="vertical" className="h-6" />
        <h1 className="text-lg font-semibold">Channel allotments</h1>
        <div className="ml-auto flex items-center gap-2">
          <ConnectionStatus />
          <ThemeToggle />
          <ChatToggleButton />
        </div>
      </header>
      <div className="flex-1 space-y-6 p-6">
        <Button variant="ghost" size="sm" className="-ml-2" asChild>
          <Link href="/app/hotel/inventory/restrictions">
            <ArrowLeft className="mr-1 h-4 w-4" />
            Restrictions
          </Link>
        </Button>
        <HotelOpsHeader
          title="Allotment / allocation by channel"
          subtitle="Cap sellable rooms pushed to each OTA for a room type and date range"
          perms={perms}
        />
        {!canEdit ? (
          <p className="text-sm text-muted-foreground">Manager access required.</p>
        ) : (
          <div className="grid gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Set allotment</CardTitle>
                <CardDescription>
                  Outbound availability for the OTA is min(property sellable, allotment).
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div>
                  <Label>OTA</Label>
                  <Select value={ota} onValueChange={setOta}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="booking_com">Booking.com</SelectItem>
                      <SelectItem value="expedia">Expedia</SelectItem>
                      <SelectItem value="agoda">Agoda</SelectItem>
                      <SelectItem value="airbnb">Airbnb</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div>
                  <Label>Room type</Label>
                  <Select value={roomTypeId || "__none__"} onValueChange={(v) => setRoomTypeId(v === "__none__" ? "" : v)}>
                    <SelectTrigger>
                      <SelectValue placeholder="Select room type" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__none__">Select…</SelectItem>
                      {types.map((t) => (
                        <SelectItem key={t.id} value={t.id}>
                          {t.name} ({t.code})
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <Label>From</Label>
                    <Input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
                  </div>
                  <div>
                    <Label>To</Label>
                    <Input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
                  </div>
                </div>
                <div>
                  <Label>Allotment (rooms)</Label>
                  <Input
                    type="number"
                    min={0}
                    value={allotment}
                    onChange={(e) => setAllotment(e.target.value)}
                  />
                </div>
                <Button disabled={busy || !hotelId} onClick={onSave}>
                  {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                  Save allotment
                </Button>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Current allotments</CardTitle>
              </CardHeader>
              <CardContent>
                {listQ.isLoading ? (
                  <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                ) : (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Date</TableHead>
                        <TableHead>OTA</TableHead>
                        <TableHead>Room type</TableHead>
                        <TableHead className="text-right">Allotment</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {(listQ.data || []).length === 0 ? (
                        <TableRow>
                          <TableCell colSpan={4} className="text-muted-foreground">
                            No allotments in this range.
                          </TableCell>
                        </TableRow>
                      ) : (
                        (listQ.data || []).map((row) => (
                          <TableRow key={row.id}>
                            <TableCell>{row.stay_date}</TableCell>
                            <TableCell>{row.ota_label || row.ota}</TableCell>
                            <TableCell className="font-mono text-xs">
                              {types.find((t) => t.id === row.room_type_id)?.name ||
                                row.room_type_id.slice(0, 8)}
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {row.allotment}
                            </TableCell>
                          </TableRow>
                        ))
                      )}
                    </TableBody>
                  </Table>
                )}
              </CardContent>
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}
