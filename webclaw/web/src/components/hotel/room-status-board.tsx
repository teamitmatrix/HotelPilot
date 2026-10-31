"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import {
  BOARD_STATUS_FILTERS,
  BOARD_STATUS_LABEL,
  HOUSEKEEPING_STATUSES,
  ROOM_STATUS_CLASS,
  type HotelRoom,
  type Reservation,
  type RoomStatus,
  guestName,
} from "@/lib/hospitality";
import { BedDouble, LogIn, LogOut, Sparkles } from "lucide-react";

export function RoomStatusBoard({
  rooms,
  reservations,
  filter,
  onFilterChange,
  canToggleStatus,
  canCheckIn,
  canCheckOut,
  canAssign,
  onToggleStatus,
  onCheckInRoom,
  onCheckOutRoom,
  onAssignRoom,
  pending,
}: {
  rooms: HotelRoom[];
  reservations: Reservation[];
  filter: RoomStatus | "all";
  onFilterChange: (value: RoomStatus | "all") => void;
  canToggleStatus: boolean;
  canCheckIn: boolean;
  canCheckOut?: boolean;
  canAssign: boolean;
  onToggleStatus: (room: HotelRoom, status: string) => void;
  onCheckInRoom: (room: HotelRoom) => void;
  onCheckOutRoom?: (room: HotelRoom) => void;
  onAssignRoom: (room: HotelRoom) => void;
  pending?: boolean;
}) {
  const counts = (Object.keys(BOARD_STATUS_LABEL) as RoomStatus[]).reduce(
    (acc, status) => {
      acc[status] = rooms.filter((r) => r.status === status).length;
      return acc;
    },
    {} as Record<RoomStatus, number>,
  );

  const visible = filter === "all" ? rooms : rooms.filter((r) => r.status === filter);

  const guestForRoom = (roomId: string) =>
    reservations.find(
      (r) =>
        r.room_id === roomId &&
        (r.status === "checked_in" || r.status === "reserved"),
    );

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3 space-y-0 pb-3">
        <CardTitle className="text-base">Room board</CardTitle>
        <div className="flex flex-wrap items-center gap-2">
          {BOARD_STATUS_FILTERS.map((opt) => {
            const active = filter === opt.value;
            const count =
              opt.value === "all"
                ? rooms.length
                : counts[opt.value as RoomStatus] ?? 0;
            return (
              <Button
                key={opt.value}
                type="button"
                size="sm"
                variant={active ? "default" : "outline"}
                className="h-7 gap-1.5 text-xs"
                onClick={() => onFilterChange(opt.value)}
              >
                {opt.label}
                <span className="tabular-nums opacity-80">{count}</span>
              </Button>
            );
          })}
        </div>
      </CardHeader>
      <CardContent>
        {visible.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            No rooms match this filter.
          </p>
        ) : (
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5">
            {visible.map((room) => {
              const guest = guestForRoom(room.id);
              const vacantReady = room.status === "vacant";
              return (
                <div
                  key={room.id}
                  className={cn(
                    "flex flex-col gap-2 rounded-lg border p-3 transition-colors",
                    room.status === "vacant" && "border-emerald-200/80 dark:border-emerald-900",
                    room.status === "dirty" && "border-amber-200/80 dark:border-amber-900",
                    room.status === "occupied" && "border-blue-200/80 dark:border-blue-900",
                    room.status === "out_of_order" && "border-red-200/80 dark:border-red-900",
                  )}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5">
                        <BedDouble className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                        <span className="truncate font-semibold">{room.room_number}</span>
                      </div>
                      <p className="mt-0.5 truncate text-xs text-muted-foreground">
                        {room.room_type_name || "Room"}
                        {room.floor ? ` · Fl ${room.floor}` : ""}
                      </p>
                    </div>
                    <Badge className={cn("shrink-0", ROOM_STATUS_CLASS[room.status])} variant="secondary">
                      {BOARD_STATUS_LABEL[room.status]}
                    </Badge>
                  </div>

                  {guest ? (
                    <p className="truncate text-xs">
                      <span className="text-muted-foreground">Guest · </span>
                      {guestName(guest)}
                    </p>
                  ) : (
                    <p className="text-xs text-muted-foreground">Empty</p>
                  )}

                  <div className="mt-auto flex flex-wrap gap-1.5 pt-1">
                    {canToggleStatus && room.status !== "occupied" ? (
                      <Select
                        value={
                          room.status === "vacant"
                            ? "clean"
                            : room.status === "out_of_order"
                              ? "maintenance"
                              : room.status
                        }
                        onValueChange={(v) => onToggleStatus(room, v)}
                        disabled={pending}
                      >
                        <SelectTrigger size="sm" className="h-7 flex-1 text-xs">
                          <SelectValue placeholder="Housekeeping" />
                        </SelectTrigger>
                        <SelectContent>
                          {HOUSEKEEPING_STATUSES.map((opt) => (
                            <SelectItem key={opt.value} value={opt.value}>
                              {opt.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    ) : null}

                    {canCheckIn && vacantReady ? (
                      <Button
                        type="button"
                        size="sm"
                        variant="secondary"
                        className="h-7 gap-1 text-xs"
                        disabled={pending}
                        onClick={() => onCheckInRoom(room)}
                      >
                        <LogIn className="h-3 w-3" />
                        Check in
                      </Button>
                    ) : null}

                    {canCheckOut &&
                    onCheckOutRoom &&
                    room.status === "occupied" &&
                    guest?.status === "checked_in" ? (
                      <Button
                        type="button"
                        size="sm"
                        variant="secondary"
                        className="h-7 gap-1 text-xs"
                        disabled={pending}
                        onClick={() => onCheckOutRoom(room)}
                      >
                        <LogOut className="h-3 w-3" />
                        Check out
                      </Button>
                    ) : null}

                    {canAssign && room.status === "vacant" && !guest ? (
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        className="h-7 gap-1 text-xs"
                        disabled={pending}
                        onClick={() => onAssignRoom(room)}
                      >
                        <Sparkles className="h-3 w-3" />
                        Assign
                      </Button>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
