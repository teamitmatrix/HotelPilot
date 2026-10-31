"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import {
  HOUSEKEEPING_CARD_RING,
  HOUSEKEEPING_FILTERS,
  HOUSEKEEPING_STATUS_BADGE,
  ROOM_STATUS_LABEL,
  addDays,
  housekeepingPrimaryAction,
  isoDate,
  matchesHousekeepingFilter,
  type HotelRoom,
  type HousekeepingFilter,
  type HousekeepingRoomStatusUpdate,
  type RoomStatus,
} from "@/lib/hospitality";
import { readInProgressRoomIds, setRoomInProgress } from "@/lib/housekeeping-local";
import { HousekeepingRoomSheet } from "@/components/hotel/housekeeping-room-sheet";
import {
  BedDouble,
  CheckCircle2,
  ChevronRight,
  Loader2,
  PlayCircle,
  RefreshCw,
  Wrench,
} from "lucide-react";

export type RoomStatusUpdate = HousekeepingRoomStatusUpdate;

const touchBtn =
  "min-h-12 min-w-12 touch-manipulation transition-transform active:scale-[0.98] disabled:active:scale-100";

function primaryShortLabel(status: string, full: string): string {
  if (status === "clean" && full.includes("Inspected")) return "Mark Clean";
  if (status === "clean" && full.includes("service")) return "Back in service";
  if (status === "dirty") return "Mark Dirty";
  return full;
}

function badgeLabel(status: RoomStatus): string {
  if (status === "vacant") return "Clean & Inspected";
  return ROOM_STATUS_LABEL[status];
}

export function HousekeepingTaskList({
  rooms,
  filter,
  onFilterChange,
  onUpdateStatus,
  pendingRoomId,
  onRefresh,
  isRefreshing,
}: {
  rooms: HotelRoom[];
  filter: HousekeepingFilter;
  onFilterChange: (value: HousekeepingFilter) => void;
  onUpdateStatus: (room: HotelRoom, update: RoomStatusUpdate) => void;
  pendingRoomId?: string | null;
  onRefresh?: () => void;
  isRefreshing?: boolean;
}) {
  const [scheduleRoom, setScheduleRoom] = useState<HotelRoom | null>(null);
  const [scheduleDate, setScheduleDate] = useState("");
  const [sheetRoom, setSheetRoom] = useState<HotelRoom | null>(null);
  const [inProgressIds, setInProgressIds] = useState<Set<string>>(() => new Set());
  const [pullOffset, setPullOffset] = useState(0);
  const pullStartY = useRef<number | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setInProgressIds(readInProgressRoomIds());
  }, []);

  const handleInProgressChange = useCallback((roomId: string, value: boolean) => {
    const next = setRoomInProgress(roomId, value);
    setInProgressIds(new Set(next));
  }, []);

  const filterCtx = useMemo(() => ({ inProgressRoomIds: inProgressIds }), [inProgressIds]);

  const counts = HOUSEKEEPING_FILTERS.reduce(
    (acc, opt) => {
      if (opt.value === "all") {
        acc[opt.value] = rooms.length;
      } else if (opt.value === "in_progress") {
        acc[opt.value] = rooms.filter((r) => inProgressIds.has(r.id)).length;
      } else {
        acc[opt.value] = rooms.filter((r) =>
          matchesHousekeepingFilter(r.status, opt.value, { roomId: r.id, ...filterCtx }),
        ).length;
      }
      return acc;
    },
    {} as Record<HousekeepingFilter, number>,
  );

  const visible = rooms
    .filter((r) => {
      if (filter === "in_progress") return inProgressIds.has(r.id);
      return matchesHousekeepingFilter(r.status, filter, { roomId: r.id, ...filterCtx });
    })
    .slice()
    .sort((a, b) => {
      const rank = (s: RoomStatus) =>
        s === "dirty" ? 0 : s === "out_of_order" ? 1 : s === "vacant" ? 2 : 3;
      const d = rank(a.status) - rank(b.status);
      if (d !== 0) return d;
      return String(a.room_number).localeCompare(String(b.room_number), undefined, {
        numeric: true,
      });
    });

  const schedulePreview = useMemo(() => {
    if (!scheduleDate) return null;
    return isoDate(addDays(new Date(`${scheduleDate}T00:00:00`), 1));
  }, [scheduleDate]);

  function openCleaningSchedule(room: HotelRoom) {
    setScheduleRoom(room);
    setScheduleDate(isoDate(addDays(new Date(), 1)));
  }

  function confirmSchedule() {
    if (!scheduleRoom || !scheduleDate) return;
    onUpdateStatus(scheduleRoom, { status: "dirty", cleaning_date: scheduleDate });
    setScheduleRoom(null);
  }

  function onTouchStart(e: React.TouchEvent) {
    const el = listRef.current;
    if (!onRefresh || !el || el.scrollTop > 0) return;
    pullStartY.current = e.touches[0]?.clientY ?? null;
  }

  function onTouchMove(e: React.TouchEvent) {
    if (pullStartY.current == null || !onRefresh) return;
    const y = e.touches[0]?.clientY ?? pullStartY.current;
    const delta = Math.max(0, Math.min(72, y - pullStartY.current));
    if (delta > 0) setPullOffset(delta);
  }

  function onTouchEnd() {
    if (pullOffset >= 56 && onRefresh && !isRefreshing) onRefresh();
    pullStartY.current = null;
    setPullOffset(0);
  }

  return (
    <div
      ref={listRef}
      className="relative space-y-3 md:space-y-4"
      onTouchStart={onTouchStart}
      onTouchMove={onTouchMove}
      onTouchEnd={onTouchEnd}
    >
      {(pullOffset > 0 || isRefreshing) && onRefresh ? (
        <div
          className="pointer-events-none flex justify-center py-1 text-xs text-muted-foreground md:hidden"
          style={{ height: isRefreshing ? 32 : pullOffset }}
        >
          <RefreshCw
            className={cn("h-5 w-5", isRefreshing && "animate-spin")}
            aria-hidden
          />
        </div>
      ) : null}

      {/* Sticky filter bar — mobile-first horizontal scroll */}
      <div className="sticky top-0 z-10 -mx-3 border-b bg-background/95 px-3 py-2.5 backdrop-blur supports-[backdrop-filter]:bg-background/90 md:static md:mx-0 md:border-0 md:bg-transparent md:p-0 md:backdrop-blur-none">
        <div className="flex gap-2 overflow-x-auto pb-0.5 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {HOUSEKEEPING_FILTERS.map((opt) => {
            const active = filter === opt.value;
            return (
              <Button
                key={opt.value}
                type="button"
                size="sm"
                variant={active ? "default" : "outline"}
                className={cn(
                  touchBtn,
                  "h-12 shrink-0 gap-1.5 rounded-full px-4 text-sm font-medium",
                )}
                onClick={() => onFilterChange(opt.value)}
              >
                <span>{opt.shortLabel}</span>
                <span
                  className={cn(
                    "min-w-[1.35rem] rounded-full px-1.5 text-center text-xs tabular-nums",
                    active ? "bg-primary-foreground/20" : "bg-muted",
                  )}
                >
                  {counts[opt.value]}
                </span>
              </Button>
            );
          })}
        </div>
      </div>

      {onRefresh ? (
        <Button
          type="button"
          size="icon"
          variant="secondary"
          className={cn(
            touchBtn,
            "fixed bottom-[max(1.25rem,env(safe-area-inset-bottom))] right-4 z-30 size-12 rounded-full shadow-lg md:hidden",
          )}
          aria-label="Refresh rooms"
          disabled={isRefreshing}
          onClick={() => onRefresh()}
        >
          <RefreshCw className={cn("h-5 w-5", isRefreshing && "animate-spin")} />
        </Button>
      ) : null}

      {visible.length === 0 ? (
        <p className="rounded-xl border border-dashed py-10 text-center text-sm text-muted-foreground">
          No rooms in this queue.
        </p>
      ) : (
        <ul className="flex flex-col gap-2.5 md:grid md:grid-cols-2 md:gap-3 xl:grid-cols-3">
          {visible.map((room) => {
            const primary = housekeepingPrimaryAction(room.status);
            const busy = pendingRoomId === room.id;
            const canAct = room.status !== "occupied";
            const inProgress = inProgressIds.has(room.id);
            const showDirty =
              room.status !== "dirty" && primary?.status !== "dirty" && canAct;
            const showMaint =
              room.status !== "out_of_order" &&
              primary?.status !== "maintenance" &&
              canAct;

            return (
              <li key={room.id}>
                <article
                  role="button"
                  tabIndex={0}
                  className={cn(
                    "rounded-xl border-2 bg-card p-3 shadow-sm transition-colors md:p-4",
                    HOUSEKEEPING_CARD_RING[room.status],
                    "cursor-pointer md:cursor-default",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  )}
                  onClick={() => {
                    if (window.matchMedia("(max-width: 767px)").matches) {
                      setSheetRoom(room);
                    }
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      if (window.matchMedia("(max-width: 767px)").matches) {
                        e.preventDefault();
                        setSheetRoom(room);
                      }
                    }
                  }}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex min-w-0 items-start gap-2">
                      <BedDouble className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                          <span className="text-xl font-bold tracking-tight">
                            {room.room_number}
                          </span>
                          {room.floor ? (
                            <span className="text-xs font-medium text-muted-foreground">
                              Fl {room.floor}
                            </span>
                          ) : null}
                        </div>
                        <p className="truncate text-sm text-muted-foreground">
                          {room.room_type_name || "Room"}
                        </p>
                        {inProgress ? (
                          <p className="text-xs font-medium text-blue-600 dark:text-blue-400">
                            Cleaning in progress
                          </p>
                        ) : null}
                        {room.available_from && room.status === "dirty" ? (
                          <p className="text-[11px] text-muted-foreground md:text-xs">
                            Bookable from {room.available_from}
                          </p>
                        ) : room.status === "out_of_order" ? (
                          <p className="text-[11px] text-muted-foreground md:text-xs">
                            Unavailable until back in service
                          </p>
                        ) : null}
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                      <Badge
                        className={cn("text-xs", HOUSEKEEPING_STATUS_BADGE[room.status])}
                        variant="secondary"
                      >
                        {badgeLabel(room.status)}
                      </Badge>
                      <ChevronRight className="h-5 w-5 text-muted-foreground md:hidden" />
                    </div>
                  </div>

                  {room.status === "occupied" ? (
                    <p className="mt-2 text-sm text-muted-foreground">
                      Guest in house — unlocks after checkout.
                    </p>
                  ) : null}

                  {canAct ? (
                    <div
                      className="mt-3 flex flex-col gap-2"
                      onClick={(e) => e.stopPropagation()}
                      onKeyDown={(e) => e.stopPropagation()}
                    >
                      {primary ? (
                        <Button
                          type="button"
                          size="lg"
                          className={cn(touchBtn, "h-12 w-full gap-2 text-base")}
                          disabled={busy}
                          onClick={() => {
                            if (primary.status === "dirty") {
                              openCleaningSchedule(room);
                            } else {
                              handleInProgressChange(room.id, false);
                              onUpdateStatus(room, { status: primary.status });
                            }
                          }}
                        >
                          {busy ? (
                            <Loader2 className="h-5 w-5 animate-spin" />
                          ) : primary.status === "clean" ? (
                            <CheckCircle2 className="h-5 w-5" />
                          ) : null}
                          {primaryShortLabel(primary.status, primary.label)}
                        </Button>
                      ) : null}

                      <div
                        className={cn(
                          "grid gap-2",
                          showDirty && showMaint
                            ? "grid-cols-2"
                            : showDirty || showMaint
                              ? "grid-cols-1"
                              : "hidden",
                        )}
                      >
                        {room.status === "dirty" ? (
                          <Button
                            type="button"
                            size="lg"
                            variant={inProgress ? "secondary" : "outline"}
                            className={cn(touchBtn, "h-12 gap-1.5")}
                            disabled={busy}
                            onClick={() => handleInProgressChange(room.id, !inProgress)}
                          >
                            <PlayCircle className="h-5 w-5" />
                            {inProgress ? "In progress" : "Start"}
                          </Button>
                        ) : null}
                        {showDirty ? (
                          <Button
                            type="button"
                            size="lg"
                            variant="outline"
                            className={cn(touchBtn, "h-12 gap-1.5")}
                            disabled={busy}
                            onClick={() => openCleaningSchedule(room)}
                          >
                            Inspect / Dirty
                          </Button>
                        ) : null}
                        {showMaint ? (
                          <Button
                            type="button"
                            size="lg"
                            variant="outline"
                            className={cn(touchBtn, "h-12 gap-1.5")}
                            disabled={busy}
                            onClick={() => onUpdateStatus(room, { status: "maintenance" })}
                          >
                            <Wrench className="h-5 w-5" />
                            Maint.
                          </Button>
                        ) : null}
                      </div>

                      <Button
                        type="button"
                        variant="ghost"
                        className="hidden h-11 w-full md:inline-flex"
                        onClick={() => setSheetRoom(room)}
                      >
                        Open details & notes
                      </Button>
                    </div>
                  ) : null}
                </article>
              </li>
            );
          })}
        </ul>
      )}

      <HousekeepingRoomSheet
        room={sheetRoom}
        open={Boolean(sheetRoom)}
        onOpenChange={(open) => {
          if (!open) setSheetRoom(null);
        }}
        busy={sheetRoom ? pendingRoomId === sheetRoom.id : false}
        inProgress={sheetRoom ? inProgressIds.has(sheetRoom.id) : false}
        onInProgressChange={handleInProgressChange}
        onUpdateStatus={onUpdateStatus}
        onOpenSchedule={openCleaningSchedule}
      />

      <Dialog
        open={Boolean(scheduleRoom)}
        onOpenChange={(open) => {
          if (!open) setScheduleRoom(null);
        }}
      >
        <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Cleaning schedule</DialogTitle>
            <DialogDescription>
              When will room {scheduleRoom?.room_number} be cleaned? Bookings can start the day
              after.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label>Cleaning date</Label>
            <Input
              type="date"
              className="h-12 text-base"
              value={scheduleDate}
              min={isoDate(new Date())}
              onChange={(e) => setScheduleDate(e.target.value)}
            />
            {schedulePreview ? (
              <p className="text-xs text-muted-foreground">
                Earliest bookable check-in:{" "}
                <span className="font-medium">{schedulePreview}</span>
              </p>
            ) : null}
          </div>
          <DialogFooter className="gap-2 sm:gap-0">
            <Button
              type="button"
              variant="outline"
              className={touchBtn}
              onClick={() => setScheduleRoom(null)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              className={touchBtn}
              disabled={!scheduleDate}
              onClick={confirmSchedule}
            >
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
