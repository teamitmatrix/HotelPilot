"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";
import { HotelOpsHeader } from "@/components/hotel/hotel-ops-header";
import { RoomStatusBoard } from "@/components/hotel/room-status-board";
import { TapeChart } from "@/components/hotel/tape-chart";
import {
  AssignRoomDialog,
  CancelReservationDialog,
  CheckInDialog,
  CheckOutDialog,
  ReservationDetailDialog,
  WalkInDialog,
  type WalkInForm,
} from "@/components/hotel/front-desk-modals";
import { useAuth } from "@/lib/auth";
import { useHotelPermissions } from "@/lib/rbac";
import { useEntityList, useEntityMutation } from "@/lib/hooks";
import { useToast } from "@/components/toast-provider";
import {
  SKILL,
  type Hotel,
  type HotelRoom,
  type Reservation,
  type RoomStatus,
  type RoomType,
  addDays,
  guestName,
  hotelCurrency,
  isoDate,
  itemsFrom,
} from "@/lib/hospitality";
import { Loader2, LogIn, LogOut, Sparkles, UserPlus, XCircle } from "lucide-react";

function startOfDay(d: Date): Date {
  const copy = new Date(d);
  copy.setHours(0, 0, 0, 0);
  return copy;
}

export default function HotelDeskPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const { showToast } = useToast();
  const mutate = useEntityMutation(SKILL);
  const router = useRouter();

  const [rangeStart, setRangeStart] = useState(() => startOfDay(new Date()));
  const [statusFilter, setStatusFilter] = useState<RoomStatus | "all">("all");

  const fromDate = isoDate(addDays(new Date(), -7));
  const toDate = isoDate(addDays(new Date(), 366));

  const reservationsQ = useEntityList(
    SKILL,
    "list-reservations",
    { limit: "500", from_date: fromDate, to_date: toDate },
    { enabled: perms.canSeeFrontDesk, refetchInterval: 8_000 },
  );
  const roomsQ = useEntityList(
    SKILL,
    "list-rooms",
    { limit: "500" },
    { enabled: perms.canSeeRooms || perms.canSeeFrontDesk, refetchInterval: 8_000 },
  );
  const typesQ = useEntityList(
    SKILL,
    "list-room-types",
    { limit: "200" },
    { enabled: perms.canWriteBookings },
  );
  const hotelsQ = useEntityList(
    SKILL,
    "list-hotels",
    { limit: "20" },
    { enabled: perms.canWriteBookings || perms.canSeeFrontDesk },
  );

  const reservations = itemsFrom<Reservation>(reservationsQ.data, "reservations");
  const rooms = useMemo(() => {
    const list = itemsFrom<HotelRoom>(roomsQ.data, "rooms");
    return [...list].sort((a, b) =>
      String(a.room_number).localeCompare(String(b.room_number), undefined, {
        numeric: true,
      }),
    );
  }, [roomsQ.data]);
  const roomTypes = itemsFrom<RoomType>(typesQ.data, "room_types");
  const hotels = itemsFrom<Hotel>(hotelsQ.data, "hotels");
  const hotelId = hotels[0]?.id ?? "";
  const currency = hotelCurrency(hotels[0]?.currency);

  const arrivals = reservations.filter(
    (r) => r.status === "reserved" || r.status === "pre_checked_in",
  );
  const inHouse = reservations.filter((r) => r.status === "checked_in");
  const checkoutRequests = inHouse.filter((r) => Boolean(r.checkout_requested_at));
  const vacantClean = rooms.filter((r) => r.status === "vacant");

  const [checkInOpen, setCheckInOpen] = useState(false);
  const [checkOutOpen, setCheckOutOpen] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [cancelReason, setCancelReason] = useState("");
  const [assignOpen, setAssignOpen] = useState(false);
  const [walkInOpen, setWalkInOpen] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const [selectedRes, setSelectedRes] = useState<Reservation | null>(null);
  const [roomId, setRoomId] = useState("");

  const [walkIn, setWalkIn] = useState<WalkInForm>(() => {
    const today = isoDate(new Date());
    return {
      first_name: "",
      last_name: "",
      email: "",
      phone: "",
      room_type_id: "",
      room_id: "",
      check_in: today,
      check_out: isoDate(addDays(new Date(), 1)),
      adults: "1",
      check_in_now: false,
    };
  });

  function openCheckIn(res?: Reservation, preferredRoom?: HotelRoom) {
    const next = res ?? null;
    setSelectedRes(next);
    setRoomId(preferredRoom?.id || next?.room_id || "");
    setCheckInOpen(true);
  }

  function openCheckOut(res?: Reservation) {
    setSelectedRes(res ?? null);
    setCheckOutOpen(true);
  }

  function openCancel(res?: Reservation) {
    setSelectedRes(res ?? null);
    setCancelReason("");
    setCancelOpen(true);
  }

  function openAssign(res?: Reservation, preferredRoom?: HotelRoom) {
    setSelectedRes(res ?? null);
    setRoomId(preferredRoom?.id || res?.room_id || "");
    setAssignOpen(true);
  }

  function openDetail(res: Reservation) {
    setSelectedRes(res);
    setDetailOpen(true);
  }

  function openWalkIn() {
    const today = isoDate(new Date());
    setWalkIn({
      first_name: "",
      last_name: "",
      email: "",
      phone: "",
      room_type_id: roomTypes[0]?.id ?? "",
      room_id: "",
      check_in: today,
      check_out: isoDate(addDays(new Date(), 1)),
      adults: "1",
      check_in_now: false,
    });
    setWalkInOpen(true);
  }

  async function submitCheckIn() {
    if (!selectedRes) return;
    const assigned = roomId || selectedRes.room_id;
    if (!assigned) {
      showToast({ type: "error", message: "Assign a room before check-in" });
      return;
    }
    try {
      await mutate.mutateAsync({
        action: "check-in",
        body: { id: selectedRes.id, room_id: assigned },
      });
      showToast({ type: "success", message: `${guestName(selectedRes)} checked in` });
      setCheckInOpen(false);
      setDetailOpen(false);
    } catch (err) {
      showToast({
        type: "error",
        message: err instanceof Error ? err.message : "Check-in failed",
      });
    }
  }

  async function submitCheckOut() {
    if (!selectedRes) return;
    const balance = Number(selectedRes.folio_balance ?? 0);
    if (Number.isFinite(balance) && balance > 0) {
      showToast({
        type: "error",
        message: "Settle the folio in Billing before check-out",
      });
      return;
    }
    try {
      await mutate.mutateAsync({
        action: "check-out",
        body: { id: selectedRes.id },
      });
      showToast({
        type: "success",
        message: `${guestName(selectedRes)} checked out — room sent to housekeeping`,
      });
      setCheckOutOpen(false);
      setDetailOpen(false);
    } catch (err) {
      showToast({
        type: "error",
        message: err instanceof Error ? err.message : "Check-out failed",
      });
    }
  }

  async function submitCancel() {
    if (!selectedRes) return;
    if (selectedRes.status !== "reserved" && selectedRes.status !== "pre_checked_in") {
      showToast({
        type: "error",
        message: "Only reserved or pre-checked-in bookings can be cancelled before check-in",
      });
      return;
    }
    try {
      await mutate.mutateAsync({
        action: "cancel-reservation",
        body: {
          id: selectedRes.id,
          ...(cancelReason.trim() ? { notes: cancelReason.trim() } : {}),
        },
      });
      showToast({
        type: "success",
        message: `Reservation for ${guestName(selectedRes)} cancelled`,
      });
      setCancelOpen(false);
      setDetailOpen(false);
      setCancelReason("");
    } catch (err) {
      showToast({
        type: "error",
        message: err instanceof Error ? err.message : "Cancel failed",
      });
    }
  }

  async function submitAssign() {
    if (!selectedRes || !roomId) return;
    try {
      await mutate.mutateAsync({
        action: "assign-room",
        body: { id: selectedRes.id, room_id: roomId },
      });
      showToast({ type: "success", message: `Room assigned to ${guestName(selectedRes)}` });
      setAssignOpen(false);
      setDetailOpen(false);
    } catch (err) {
      showToast({
        type: "error",
        message: err instanceof Error ? err.message : "Assign failed",
      });
    }
  }

  async function assignFromChart(reservationId: string, nextRoomId: string) {
    try {
      await mutate.mutateAsync({
        action: "assign-room",
        body: { id: reservationId, room_id: nextRoomId },
      });
      const res = reservations.find((r) => r.id === reservationId);
      showToast({
        type: "success",
        message: res ? `Room updated for ${guestName(res)}` : "Room assigned",
      });
    } catch (err) {
      showToast({
        type: "error",
        message: err instanceof Error ? err.message : "Could not move reservation",
      });
    }
  }

  async function toggleRoomStatus(room: HotelRoom, status: string) {
    try {
      const body: Record<string, unknown> = { id: room.id, status };
      if (status === "dirty") {
        // Cleaned tomorrow → bookable day after tomorrow
        body.cleaning_date = isoDate(addDays(new Date(), 1));
      }
      await mutate.mutateAsync({
        action: "update-room-status",
        body,
      });
      showToast({ type: "success", message: `Room ${room.room_number} updated` });
    } catch (err) {
      showToast({
        type: "error",
        message: err instanceof Error ? err.message : "Update failed",
      });
    }
  }

  async function submitWalkIn() {
    if (!hotelId) {
      showToast({ type: "error", message: "No hotel found for this property" });
      return;
    }
    if (walkIn.check_out <= walkIn.check_in) {
      showToast({ type: "error", message: "Check-out must be after check-in" });
      return;
    }
    try {
      const guestRes = await mutate.mutateAsync({
        action: "add-guest",
        body: {
          first_name: walkIn.first_name.trim(),
          last_name: walkIn.last_name.trim(),
          email: walkIn.email.trim() || undefined,
          phone: walkIn.phone.trim() || undefined,
        },
      });
      const guestId = String(guestRes.guest_id || "");
      if (!guestId) throw new Error("Guest was created without an id");

      const booking = await mutate.mutateAsync({
        action: "add-reservation",
        body: {
          hotel_id: hotelId,
          guest_id: guestId,
          room_type_id: walkIn.room_type_id,
          room_id: walkIn.room_id || undefined,
          check_in: walkIn.check_in,
          check_out: walkIn.check_out,
          adults: walkIn.adults,
        },
      });
      const reservationId = String(booking.reservation_id || "");

      if (walkIn.check_in_now) {
        if (!walkIn.room_id) {
          showToast({
            type: "error",
            message: "Pick a room to check the walk-in in now",
          });
          return;
        }
        await mutate.mutateAsync({
          action: "check-in",
          body: { id: reservationId, room_id: walkIn.room_id },
        });
        showToast({
          type: "success",
          message: `${walkIn.first_name} checked in`,
        });
      } else {
        showToast({
          type: "success",
          message: `Walk-in booked for ${walkIn.first_name} ${walkIn.last_name}`,
        });
      }
      setWalkInOpen(false);
    } catch (err) {
      showToast({
        type: "error",
        message: err instanceof Error ? err.message : "Walk-in failed",
      });
    }
  }

  const loading = reservationsQ.isLoading || roomsQ.isLoading;
  const canDesk = perms.canSeeFrontDesk;

  if (!canDesk) {
    return (
      <div className="flex flex-1 flex-col">
        <header className="flex h-14 items-center gap-4 border-b bg-background px-6">
          <SidebarTrigger />
          <Separator orientation="vertical" className="h-6" />
          <h1 className="text-lg font-semibold">Front desk</h1>
        </header>
        <div className="flex flex-1 items-center justify-center p-6 text-center text-sm text-muted-foreground">
          Front desk is not available for your role. Use Housekeeping for room status updates.
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col">
      <header className="flex h-14 items-center gap-4 border-b bg-background px-6">
        <SidebarTrigger />
        <Separator orientation="vertical" className="h-6" />
        <h1 className="text-lg font-semibold">Front desk</h1>
        <div className="ml-auto flex items-center gap-2">
          <ConnectionStatus />
          <ThemeToggle />
          <ChatToggleButton />
        </div>
      </header>

      <div className="flex-1 space-y-6 p-6">
        <HotelOpsHeader
          title="Front desk board"
          subtitle="Room status, walk-ins, and the reservation tape chart"
          perms={perms}
          actions={
            <div className="flex flex-wrap gap-2">
              {perms.canCheckIn ? (
                <Button size="sm" className="gap-1.5" onClick={() => openCheckIn()}>
                  <LogIn className="h-3.5 w-3.5" />
                  Check in
                </Button>
              ) : null}
              {perms.canCheckOut ? (
                <Button
                  size="sm"
                  variant="outline"
                  className="gap-1.5"
                  onClick={() => openCheckOut()}
                >
                  <LogOut className="h-3.5 w-3.5" />
                  Check out
                </Button>
              ) : null}
              {perms.canWriteBookings ? (
                <Button
                  size="sm"
                  variant="outline"
                  className="gap-1.5"
                  onClick={() => openCancel()}
                >
                  <XCircle className="h-3.5 w-3.5" />
                  Cancel booking
                </Button>
              ) : null}
              {perms.canWriteBookings ? (
                <Button
                  size="sm"
                  variant="outline"
                  className="gap-1.5"
                  onClick={() => openAssign()}
                >
                  <Sparkles className="h-3.5 w-3.5" />
                  Assign room
                </Button>
              ) : null}
              {perms.canWriteBookings ? (
                <Button
                  size="sm"
                  variant="secondary"
                  className="gap-1.5"
                  onClick={openWalkIn}
                >
                  <UserPlus className="h-3.5 w-3.5" />
                  Walk-in
                </Button>
              ) : null}
            </div>
          }
        />

        {!canDesk ? (
          <p className="text-sm text-muted-foreground">
            Front desk operations are not available for your role.
          </p>
        ) : loading ? (
          <div className="flex h-48 items-center justify-center">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <div className="space-y-6">
            {(arrivals.some((r) => r.status === "pre_checked_in") ||
              checkoutRequests.length > 0) && (
              <div className="flex flex-wrap gap-2 text-sm">
                {arrivals.filter((r) => r.status === "pre_checked_in").length > 0 ? (
                  <div className="rounded-md border border-violet-200 bg-violet-50 px-3 py-2 text-violet-900 dark:border-violet-900 dark:bg-violet-950/40 dark:text-violet-100">
                    {
                      arrivals.filter((r) => r.status === "pre_checked_in").length
                    }{" "}
                    guest
                    {arrivals.filter((r) => r.status === "pre_checked_in").length === 1
                      ? ""
                      : "s"}{" "}
                    pre-checked in via guest portal
                  </div>
                ) : null}
                {checkoutRequests.length > 0 ? (
                  <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-amber-950 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-100">
                    {checkoutRequests.length} checkout request
                    {checkoutRequests.length === 1 ? "" : "s"} waiting
                  </div>
                ) : null}
              </div>
            )}

            {perms.canSeeRooms ? (
              <RoomStatusBoard
                rooms={rooms}
                reservations={reservations}
                filter={statusFilter}
                onFilterChange={setStatusFilter}
                canToggleStatus={perms.canToggleRoomStatus}
                canCheckIn={perms.canCheckIn}
                canCheckOut={perms.canCheckOut}
                canAssign={perms.canWriteBookings}
                onToggleStatus={toggleRoomStatus}
                onCheckInRoom={(room) => openCheckIn(undefined, room)}
                onCheckOutRoom={(room) => {
                  const guest = reservations.find(
                    (r) => r.room_id === room.id && r.status === "checked_in",
                  );
                  openCheckOut(guest);
                }}
                onAssignRoom={(room) => openAssign(undefined, room)}
                pending={mutate.isPending}
              />
            ) : null}

            {perms.canSeeFrontDesk ? (
              <TapeChart
                rooms={rooms}
                reservations={reservations}
                rangeStart={rangeStart}
                onRangeChange={setRangeStart}
                canAssign={perms.canWriteBookings}
                onAssign={assignFromChart}
                onOpenReservation={openDetail}
              />
            ) : null}
          </div>
        )}
      </div>

      <CheckInDialog
        open={checkInOpen}
        onOpenChange={setCheckInOpen}
        arrivals={arrivals}
        vacantRooms={vacantClean}
        reservations={reservations}
        selected={selectedRes}
        roomId={roomId}
        onSelectReservation={(res) => {
          setSelectedRes(res);
          setRoomId(res?.room_id || "");
        }}
        onRoomChange={setRoomId}
        onSubmit={submitCheckIn}
        pending={mutate.isPending}
      />

      <CheckOutDialog
        open={checkOutOpen}
        onOpenChange={setCheckOutOpen}
        inHouse={inHouse}
        selected={selectedRes?.status === "checked_in" ? selectedRes : null}
        currency={currency}
        onSelectReservation={setSelectedRes}
        onSubmit={submitCheckOut}
        pending={mutate.isPending}
        onOpenBilling={() => {
          setCheckOutOpen(false);
          router.push("/app/hotel/billing");
        }}
      />

      <CancelReservationDialog
        open={cancelOpen}
        onOpenChange={setCancelOpen}
        arrivals={arrivals}
        selected={
          selectedRes?.status === "reserved" || selectedRes?.status === "pre_checked_in"
            ? selectedRes
            : null
        }
        reason={cancelReason}
        onReasonChange={setCancelReason}
        onSelectReservation={setSelectedRes}
        onSubmit={submitCancel}
        pending={mutate.isPending}
      />

      <AssignRoomDialog
        open={assignOpen}
        onOpenChange={setAssignOpen}
        arrivals={arrivals}
        rooms={rooms}
        reservations={reservations}
        selected={selectedRes}
        roomId={roomId}
        onSelectReservation={(res) => {
          setSelectedRes(res);
          setRoomId(res?.room_id || roomId);
        }}
        onRoomChange={setRoomId}
        onSubmit={submitAssign}
        pending={mutate.isPending}
      />

      <WalkInDialog
        open={walkInOpen}
        onOpenChange={setWalkInOpen}
        form={walkIn}
        onChange={setWalkIn}
        roomTypes={roomTypes}
        rooms={rooms}
        reservations={reservations}
        hotelId={hotelId}
        currency={currency}
        onSubmit={submitWalkIn}
        pending={mutate.isPending}
      />

      <ReservationDetailDialog
        open={detailOpen}
        onOpenChange={setDetailOpen}
        reservation={selectedRes}
        currency={currency}
        canCheckIn={perms.canCheckIn}
        canCheckOut={perms.canCheckOut}
        canCancel={perms.canWriteBookings}
        canAssign={perms.canWriteBookings}
        onCheckIn={() => {
          setDetailOpen(false);
          openCheckIn(selectedRes ?? undefined);
        }}
        onCheckOut={() => {
          setDetailOpen(false);
          openCheckOut(selectedRes ?? undefined);
        }}
        onCancel={() => {
          setDetailOpen(false);
          openCancel(selectedRes ?? undefined);
        }}
        onAssign={() => {
          setDetailOpen(false);
          openAssign(selectedRes ?? undefined);
        }}
      />
    </div>
  );
}
