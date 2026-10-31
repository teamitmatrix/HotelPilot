"use client";

import { useState } from "react";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { Separator } from "@/components/ui/separator";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";
import { HotelOpsHeader } from "@/components/hotel/hotel-ops-header";
import { HousekeepingTaskList } from "@/components/hotel/housekeeping-task-list";
import { useAuth } from "@/lib/auth";
import { useHotelPermissions } from "@/lib/rbac";
import { useEntityList, useEntityMutation } from "@/lib/hooks";
import { useToast } from "@/components/toast-provider";
import {
  SKILL,
  type HotelRoom,
  type HousekeepingFilter,
  ROOM_STATUS_LABEL,
  itemsFrom,
} from "@/lib/hospitality";
import { Loader2 } from "lucide-react";

const POLL_MS = 8_000;

export default function HotelHousekeepingPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const { showToast } = useToast();
  const mutate = useEntityMutation(SKILL);
  const canAccess = perms.canToggleRoomStatus || perms.canSeeRooms;

  const [filter, setFilter] = useState<HousekeepingFilter>("needs_cleaning");
  const [pendingRoomId, setPendingRoomId] = useState<string | null>(null);

  const roomsQ = useEntityList(
    SKILL,
    "list-rooms",
    { limit: "500" },
    { enabled: canAccess, refetchInterval: POLL_MS },
  );

  async function refreshRooms() {
    await roomsQ.refetch();
  }
  const rooms = itemsFrom<HotelRoom>(roomsQ.data, "rooms");

  async function updateStatus(
    room: HotelRoom,
    update: { status: string; cleaning_date?: string; available_from?: string },
  ) {
    setPendingRoomId(room.id);
    try {
      await mutate.mutateAsync({
        action: "update-room-status",
        body: {
          id: room.id,
          status: update.status,
          ...(update.cleaning_date ? { cleaning_date: update.cleaning_date } : {}),
          ...(update.available_from ? { available_from: update.available_from } : {}),
        },
      });
      const label =
        update.status === "clean"
          ? "Clean & Inspected"
          : update.status === "maintenance"
            ? ROOM_STATUS_LABEL.out_of_order
            : ROOM_STATUS_LABEL.dirty;
      const from =
        update.available_from ||
        (update.cleaning_date ? `day after ${update.cleaning_date}` : null);
      showToast({
        type: "success",
        message: from
          ? `Room ${room.room_number} → ${label} (bookable from ${from})`
          : `Room ${room.room_number} → ${label}`,
      });
    } catch (err) {
      showToast({
        type: "error",
        message: err instanceof Error ? err.message : "Could not update room",
      });
    } finally {
      setPendingRoomId(null);
    }
  }

  if (!perms.canToggleRoomStatus) {
    return (
      <div className="flex flex-1 flex-col">
        <header className="flex h-12 shrink-0 items-center gap-2 border-b px-3 sm:h-14 sm:px-4">
          <SidebarTrigger className="-ml-1" />
          <Separator orientation="vertical" className="mr-2 h-4" />
          <span className="text-sm font-medium">Housekeeping</span>
        </header>
        <div className="flex flex-1 items-center justify-center p-6 text-center text-sm text-muted-foreground">
          Housekeeping status updates are not enabled for your role.
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="sticky top-0 z-20 flex h-12 shrink-0 items-center gap-2 border-b bg-background px-3 sm:h-14 sm:px-4">
        <SidebarTrigger className="-ml-1 size-9 touch-manipulation" />
        <Separator orientation="vertical" className="mr-1 hidden h-4 sm:mr-2 sm:block" />
        <div className="min-w-0 flex-1">
          <span className="block truncate text-sm font-semibold sm:font-medium">Housekeeping</span>
          <span className="block truncate text-[11px] text-muted-foreground sm:hidden">
            Clean & return rooms to the desk
          </span>
        </div>
        <div className="ml-auto flex items-center gap-0.5 sm:gap-1">
          <div className="hidden sm:block">
            <ConnectionStatus />
          </div>
          <div className="hidden sm:contents">
            <ThemeToggle />
          </div>
          <div className="hidden md:contents">
            <ChatToggleButton />
          </div>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-auto overscroll-y-contain px-3 pb-[max(1rem,env(safe-area-inset-bottom))] pt-3 sm:space-y-4 sm:p-6">
        {/* Desktop / tablet ops chrome; phones stay task-focused */}
        <div className="mb-3 hidden sm:block">
          <HotelOpsHeader
            title="Housekeeping"
            subtitle="Clean, inspect, and return rooms to the front desk board."
            perms={perms}
          />
        </div>

        {roomsQ.isLoading ? (
          <div className="flex justify-center py-16">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <HousekeepingTaskList
            rooms={rooms}
            filter={filter}
            onFilterChange={setFilter}
            onUpdateStatus={updateStatus}
            pendingRoomId={pendingRoomId}
            onRefresh={refreshRooms}
            isRefreshing={roomsQ.isFetching && !roomsQ.isLoading}
          />
        )}
      </div>
    </div>
  );
}
