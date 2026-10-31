"use client";

import { useMemo, useState, type CSSProperties } from "react";
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  useDraggable,
  useDroppable,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import {
  type HotelRoom,
  type Reservation,
  addDays,
  guestName,
  isoDate,
  nightCount,
  paymentStatus,
} from "@/lib/hospitality";
import { ChevronLeft, ChevronRight, GripVertical } from "lucide-react";

const DAY_W = 56;
const LABEL_W = 112;
const ROW_H = 44;

function daysBetween(a: string, b: string): number {
  const ms = Date.parse(`${b}T00:00:00`) - Date.parse(`${a}T00:00:00`);
  return Math.round(ms / 86_400_000);
}

function barClass(status: Reservation["status"]): string {
  if (status === "checked_in") return "bg-blue-500 text-white shadow-sm";
  if (status === "pre_checked_in") {
    return "bg-violet-400 text-white shadow-sm dark:bg-violet-600";
  }
  if (status === "reserved") return "bg-sky-200 text-sky-950 dark:bg-sky-800 dark:text-sky-50";
  if (status === "checked_out") return "bg-muted text-muted-foreground";
  if (status === "cancelled" || status === "no_show") {
    return "bg-muted/60 text-muted-foreground line-through opacity-80";
  }
  return "bg-muted/80 text-muted-foreground";
}

function ReservationBar({
  res,
  chartStart,
  dayCount,
  draggable,
  onOpen,
}: {
  res: Reservation;
  chartStart: string;
  dayCount: number;
  draggable: boolean;
  onOpen: (res: Reservation) => void;
}) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: `res-${res.id}`,
    data: { reservation: res },
    disabled: !draggable || (res.status !== "reserved" && res.status !== "pre_checked_in"),
  });

  const startOffset = daysBetween(chartStart, res.check_in);
  const nights = nightCount(res.check_in, res.check_out);
  if (startOffset + nights <= 0 || startOffset >= dayCount) return null;

  const left = Math.max(0, startOffset) * DAY_W + 2;
  const clippedLeft = Math.max(0, -startOffset);
  const visibleNights = Math.min(nights - clippedLeft, dayCount - Math.max(0, startOffset));
  const width = Math.max(DAY_W - 4, visibleNights * DAY_W - 4);
  const pay = paymentStatus(res);

  const style: CSSProperties = {
    left,
    width,
    transform: transform
      ? `translate3d(${transform.x}px, ${transform.y}px, 0)`
      : undefined,
    opacity: isDragging ? 0.35 : 1,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={cn(
        "absolute top-1.5 z-10 flex h-8 items-center gap-0.5 overflow-hidden rounded-md text-left text-[11px] font-medium",
        barClass(res.status),
        isDragging && "pointer-events-none",
      )}
      title={`${guestName(res)} · ${res.check_in} → ${res.check_out} · ${pay.label}`}
    >
      {draggable && (res.status === "reserved" || res.status === "pre_checked_in") ? (
        <button
          type="button"
          className="flex h-full shrink-0 cursor-grab items-center px-0.5 active:cursor-grabbing"
          aria-label="Drag to assign room"
          {...listeners}
          {...attributes}
        >
          <GripVertical className="h-3 w-3 opacity-70" />
        </button>
      ) : null}
      <button
        type="button"
        className="min-w-0 flex-1 truncate px-1.5 py-1 text-left"
        onClick={() => onOpen(res)}
      >
        {guestName(res)}
        {res.status === "pre_checked_in" ? " · Pre" : ""}
        {res.checkout_requested_at ? " · Out?" : ""}
      </button>
    </div>
  );
}

function RoomRow({
  room,
  days,
  reservations,
  chartStart,
  canDrag,
  onOpen,
}: {
  room: HotelRoom;
  days: Date[];
  reservations: Reservation[];
  chartStart: string;
  canDrag: boolean;
  onOpen: (res: Reservation) => void;
}) {
  const { setNodeRef, isOver } = useDroppable({
    id: `room-${room.id}`,
    data: { room },
  });
  const roomRes = reservations.filter(
    (r) =>
      r.room_id === room.id &&
      r.status !== "cancelled" &&
      r.status !== "no_show",
  );

  return (
    <div
      ref={setNodeRef}
      className={cn(
        "relative flex border-t border-border/70",
        isOver && "bg-primary/5",
      )}
      style={{ height: ROW_H }}
    >
      <div
        className="sticky left-0 z-20 flex shrink-0 flex-col justify-center border-r bg-background px-2"
        style={{ width: LABEL_W }}
      >
        <span className="truncate text-sm font-medium">{room.room_number}</span>
        <span className="truncate text-[10px] text-muted-foreground">
          {room.room_type_name}
        </span>
      </div>
      <div className="relative flex-1">
        <div className="absolute inset-0 flex">
          {days.map((d) => (
            <div
              key={isoDate(d)}
              className="h-full border-l border-border/40"
              style={{ width: DAY_W }}
            />
          ))}
        </div>
        {roomRes.map((res) => (
          <ReservationBar
            key={res.id}
            res={res}
            chartStart={chartStart}
            dayCount={days.length}
            draggable={canDrag}
            onOpen={onOpen}
          />
        ))}
      </div>
    </div>
  );
}

export function TapeChart({
  rooms,
  reservations,
  rangeStart,
  onRangeChange,
  canAssign,
  onAssign,
  onOpenReservation,
}: {
  rooms: HotelRoom[];
  reservations: Reservation[];
  rangeStart: Date;
  onRangeChange: (next: Date) => void;
  canAssign: boolean;
  onAssign: (reservationId: string, roomId: string) => Promise<void> | void;
  onOpenReservation: (res: Reservation) => void;
}) {
  const days = useMemo(
    () => Array.from({ length: 14 }, (_, i) => addDays(rangeStart, i)),
    [rangeStart],
  );
  const chartStart = isoDate(days[0]);
  const dayCount = days.length;
  const today = isoDate(new Date());
  const [active, setActive] = useState<Reservation | null>(null);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
  );

  const unassigned = reservations.filter(
    (r) => !r.room_id && (r.status === "reserved" || r.status === "pre_checked_in"),
  );
  const cancelled = reservations.filter((r) => {
    if (r.status !== "cancelled" && r.status !== "no_show") return false;
    const startOffset = daysBetween(chartStart, r.check_in);
    const nights = nightCount(r.check_in, r.check_out);
    return startOffset + nights > 0 && startOffset < dayCount;
  });

  function onDragStart(event: DragStartEvent) {
    const res = event.active.data.current?.reservation as Reservation | undefined;
    setActive(res ?? null);
  }

  async function onDragEnd(event: DragEndEvent) {
    setActive(null);
    const res = event.active.data.current?.reservation as Reservation | undefined;
    const room = event.over?.data.current?.room as HotelRoom | undefined;
    if (!res || !room || !canAssign) return;
    if (res.room_id === room.id) return;
    await onAssign(res.id, room.id);
  }

  return (
    <Card className="overflow-hidden">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
        <div>
          <CardTitle className="text-base">Tape chart</CardTitle>
          <p className="mt-1 text-xs text-muted-foreground">
            {canAssign
              ? "Drag a reserved stay onto a room row, or click a bar for guest & payment details."
              : "Click a reservation bar for guest details, balance, and payment status."}
          </p>
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => onRangeChange(addDays(rangeStart, -7))}
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <span className="w-44 text-center text-xs text-muted-foreground">
            {days[0].toLocaleDateString(undefined, { month: "short", day: "numeric" })}
            {" – "}
            {days[13].toLocaleDateString(undefined, { month: "short", day: "numeric" })}
          </span>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => onRangeChange(addDays(rangeStart, 7))}
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="ml-1 h-8 text-xs"
            onClick={() => {
              const d = new Date();
              d.setHours(0, 0, 0, 0);
              onRangeChange(d);
            }}
          >
            Today
          </Button>
        </div>
      </CardHeader>
      <CardContent className="px-0 pb-0">
        {unassigned.length > 0 ? (
          <div className="mx-4 mb-3 rounded-md border border-dashed bg-muted/30 px-3 py-2">
            <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              Unassigned arrivals
            </p>
            <div className="flex flex-wrap gap-1.5">
              {unassigned.map((res) => (
                <button
                  key={res.id}
                  type="button"
                  className={cn(
                    "rounded-md px-2 py-1 text-left text-xs",
                    barClass("reserved"),
                  )}
                  onClick={() => onOpenReservation(res)}
                >
                  {guestName(res)} · {res.room_type_name} · {res.check_in}
                </button>
              ))}
            </div>
          </div>
        ) : null}

        {cancelled.length > 0 ? (
          <div className="mx-4 mb-3 rounded-md border border-dashed border-muted-foreground/30 bg-muted/20 px-3 py-2">
            <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              Cancelled ({cancelled.length})
            </p>
            <div className="flex flex-wrap gap-1.5">
              {cancelled.map((res) => (
                <button
                  key={res.id}
                  type="button"
                  className={cn(
                    "rounded-md px-2 py-1 text-left text-xs",
                    barClass("cancelled"),
                  )}
                  onClick={() => onOpenReservation(res)}
                >
                  {guestName(res)} · {res.room_type_name} · {res.check_in}
                </button>
              ))}
            </div>
          </div>
        ) : null}

        <DndContext sensors={sensors} onDragStart={onDragStart} onDragEnd={onDragEnd}>
          <div className="overflow-x-auto">
            <div style={{ minWidth: LABEL_W + days.length * DAY_W }}>
              <div className="flex border-b bg-muted/20">
                <div
                  className="sticky left-0 z-20 shrink-0 border-r bg-muted/40 px-2 py-2 text-xs font-medium text-muted-foreground"
                  style={{ width: LABEL_W }}
                >
                  Room
                </div>
                {days.map((d) => {
                  const iso = isoDate(d);
                  const isToday = iso === today;
                  return (
                    <div
                      key={iso}
                      className={cn(
                        "shrink-0 py-2 text-center text-[11px]",
                        isToday ? "bg-primary/10 font-semibold text-primary" : "text-muted-foreground",
                      )}
                      style={{ width: DAY_W }}
                    >
                      <div>{d.toLocaleDateString(undefined, { weekday: "short" })}</div>
                      <div>{d.getDate()}</div>
                    </div>
                  );
                })}
              </div>

              {rooms.length === 0 ? (
                <p className="p-8 text-center text-sm text-muted-foreground">
                  No rooms yet. Add inventory under Inventory & rates.
                </p>
              ) : (
                rooms.map((room) => (
                  <RoomRow
                    key={room.id}
                    room={room}
                    days={days}
                    reservations={reservations}
                    chartStart={chartStart}
                    canDrag={canAssign}
                    onOpen={onOpenReservation}
                  />
                ))
              )}
            </div>
          </div>
          <DragOverlay>
            {active ? (
              <div
                className={cn(
                  "flex h-8 items-center rounded-md px-2 text-xs font-medium shadow-lg",
                  barClass(active.status),
                )}
              >
                {guestName(active)}
              </div>
            ) : null}
          </DragOverlay>
        </DndContext>
      </CardContent>
    </Card>
  );
}
