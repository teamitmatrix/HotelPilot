"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import {
  BOARD_STATUS_LABEL,
  HOUSEKEEPING_STATUS_BADGE,
  ROOM_STATUS_LABEL,
  addDays,
  housekeepingPrimaryAction,
  isoDate,
  type HotelRoom,
} from "@/lib/hospitality";
import {
  readHousekeepingNotes,
  saveHousekeepingNote,
  setRoomInProgress,
} from "@/lib/housekeeping-local";
import { Camera, CheckCircle2, Loader2, PlayCircle, Wrench } from "lucide-react";
import type { HousekeepingRoomStatusUpdate } from "@/lib/hospitality";

const touchBtn =
  "min-h-12 touch-manipulation transition-transform active:scale-[0.98] disabled:active:scale-100";

export function HousekeepingRoomSheet({
  room,
  open,
  onOpenChange,
  busy,
  inProgress,
  onInProgressChange,
  onUpdateStatus,
  onOpenSchedule,
}: {
  room: HotelRoom | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  busy?: boolean;
  inProgress?: boolean;
  onInProgressChange?: (roomId: string, value: boolean) => void;
  onUpdateStatus: (room: HotelRoom, update: HousekeepingRoomStatusUpdate) => void;
  onOpenSchedule: (room: HotelRoom) => void;
}) {
  const [note, setNote] = useState("");
  const [photoName, setPhotoName] = useState<string | null>(null);

  useEffect(() => {
    if (!room) {
      setNote("");
      setPhotoName(null);
      return;
    }
    const notes = readHousekeepingNotes();
    setNote(notes[room.id] ?? "");
    setPhotoName(null);
  }, [room?.id, open]);

  if (!room) return null;

  const activeRoom = room;
  const primary = housekeepingPrimaryAction(activeRoom.status);
  const canAct = activeRoom.status !== "occupied";
  const statusLabel =
    activeRoom.status === "vacant" ? "Clean & Inspected" : ROOM_STATUS_LABEL[activeRoom.status];

  function persistNote() {
    saveHousekeepingNote(activeRoom.id, note);
  }

  function appendMaintenancePhoto(file: File | null) {
    if (!file) return;
    setPhotoName(file.name);
    const stamp = new Date().toLocaleString();
    const line = `[Maintenance photo ${stamp}: ${file.name}]`;
    setNote((prev) => (prev.trim() ? `${prev.trim()}\n${line}` : line));
  }

  function handleStatus(update: HousekeepingRoomStatusUpdate) {
    persistNote();
    if (update.status === "clean" && onInProgressChange) {
      onInProgressChange(activeRoom.id, false);
      setRoomInProgress(activeRoom.id, false);
    }
    onUpdateStatus(activeRoom, update);
    onOpenChange(false);
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="bottom"
        className="max-h-[92dvh] overflow-y-auto rounded-t-2xl px-4 pb-[max(1rem,env(safe-area-inset-bottom))] pt-2 md:max-w-lg md:mx-auto"
        showCloseButton
      >
        <div className="mx-auto mb-2 h-1 w-10 shrink-0 rounded-full bg-muted md:hidden" />
        <SheetHeader className="px-0 text-left">
          <SheetTitle className="flex flex-wrap items-center gap-2 text-xl">
            Room {activeRoom.room_number}
            <Badge className={cn("text-xs", HOUSEKEEPING_STATUS_BADGE[activeRoom.status])}>
              {statusLabel}
            </Badge>
          </SheetTitle>
          <SheetDescription className="text-sm">
            {activeRoom.room_type_name || "Room"}
            {activeRoom.floor ? ` · Floor ${activeRoom.floor}` : ""}
            {inProgress ? " · Cleaning in progress" : ""}
          </SheetDescription>
        </SheetHeader>

        {activeRoom.status === "occupied" ? (
          <p className="text-sm text-muted-foreground">
            Guest in house — status changes unlock after checkout.
          </p>
        ) : null}

        {canAct ? (
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Change status
              </p>
              <div className="flex flex-col gap-2">
                {primary ? (
                  <Button
                    type="button"
                    className={cn(touchBtn, "w-full gap-2 text-base")}
                    disabled={busy}
                    onClick={() => {
                      if (primary.status === "dirty") {
                        onOpenSchedule(activeRoom);
                        onOpenChange(false);
                      } else {
                        handleStatus({ status: primary.status });
                      }
                    }}
                  >
                    {busy ? (
                      <Loader2 className="h-5 w-5 animate-spin" />
                    ) : primary.status === "clean" ? (
                      <CheckCircle2 className="h-5 w-5" />
                    ) : null}
                    {primary.label}
                  </Button>
                ) : null}

                {activeRoom.status === "dirty" && onInProgressChange ? (
                  <Button
                    type="button"
                    variant={inProgress ? "secondary" : "outline"}
                    className={cn(touchBtn, "w-full gap-2")}
                    disabled={busy}
                    onClick={() => {
                      const next = !inProgress;
                      onInProgressChange(activeRoom.id, next);
                      setRoomInProgress(activeRoom.id, next);
                    }}
                  >
                    <PlayCircle className="h-5 w-5" />
                    {inProgress ? "Cleaning in progress" : "Start cleaning (in progress)"}
                  </Button>
                ) : null}

                <div className="grid grid-cols-2 gap-2">
                  {activeRoom.status !== "dirty" && primary?.status !== "dirty" ? (
                    <Button
                      type="button"
                      variant="outline"
                      className={cn(touchBtn, "gap-1")}
                      disabled={busy}
                      onClick={() => onOpenSchedule(activeRoom)}
                    >
                      Mark dirty
                    </Button>
                  ) : null}
                  {activeRoom.status !== "out_of_order" ? (
                    <Button
                      type="button"
                      variant="outline"
                      className={cn(touchBtn, "gap-1")}
                      disabled={busy}
                      onClick={() => handleStatus({ status: "maintenance" })}
                    >
                      <Wrench className="h-4 w-4" />
                      Maintenance
                    </Button>
                  ) : null}
                </div>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="hk-note">Cleaning notes</Label>
              <Textarea
                id="hk-note"
                placeholder="Minibar restocked, extra towels…"
                className="min-h-[88px] text-base"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                onBlur={persistNote}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="hk-photo">Maintenance photo</Label>
              <div className="flex items-center gap-2">
                <Input
                  id="hk-photo"
                  type="file"
                  accept="image/*"
                  capture="environment"
                  className="hidden"
                  onChange={(e) => appendMaintenancePhoto(e.target.files?.[0] ?? null)}
                />
                <Button
                  type="button"
                  variant="outline"
                  className={cn(touchBtn, "flex-1 gap-2")}
                  onClick={() => document.getElementById("hk-photo")?.click()}
                >
                  <Camera className="h-5 w-5" />
                  Take photo
                </Button>
              </div>
              {photoName ? (
                <p className="text-xs text-muted-foreground">Attached: {photoName}</p>
              ) : null}
              <p className="text-[11px] text-muted-foreground">
                Photos are noted on this device until a maintenance ticket API is connected.
              </p>
            </div>
          </div>
        ) : null}

        <SheetFooter className="px-0 pb-2">
          <p className="w-full text-center text-[11px] text-muted-foreground">
            Board label: {BOARD_STATUS_LABEL[activeRoom.status]}
          </p>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
