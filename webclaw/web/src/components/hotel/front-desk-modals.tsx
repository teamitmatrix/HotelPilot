"use client";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PhoneInput } from "@/components/ui/phone-input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  type HotelRoom,
  type Reservation,
  type RoomType,
  ROOM_STATUS_LABEL,
  formatMoney,
  guestName,
  hotelCurrency,
  isPreArrivalStatus,
  isRoomAssignable,
  paymentStatus,
  reservationStatusLabel,
  roomTypeHasAvailability,
} from "@/lib/hospitality";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchApi, API_BASE } from "@/lib/api";
import { getAccessToken } from "@/lib/auth";
import { quoteStay } from "@/lib/rate-calendar-api";

export type WalkInForm = {
  first_name: string;
  last_name: string;
  email: string;
  phone: string;
  room_type_id: string;
  room_id: string;
  check_in: string;
  check_out: string;
  adults: string;
  check_in_now: boolean;
};

export function CheckInDialog({
  open,
  onOpenChange,
  arrivals,
  vacantRooms,
  reservations,
  selected,
  roomId,
  onSelectReservation,
  onRoomChange,
  onSubmit,
  pending,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  arrivals: Reservation[];
  vacantRooms: HotelRoom[];
  reservations: Reservation[];
  selected: Reservation | null;
  roomId: string;
  onSelectReservation: (res: Reservation | null) => void;
  onRoomChange: (id: string) => void;
  onSubmit: () => void;
  pending: boolean;
}) {
  const rooms = useMemo(() => {
    if (!selected) return vacantRooms.filter((rm) => rm.status === "vacant");
    return vacantRooms.filter(
      (rm) =>
        rm.room_type_id === selected.room_type_id &&
        isRoomAssignable({
          room: rm,
          checkIn: selected.check_in,
          checkOut: selected.check_out,
          reservations,
          immediate: true,
          excludeReservationId: selected.id,
        }),
    );
  }, [vacantRooms, selected, reservations]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Check in guest</DialogTitle>
          <DialogDescription>
            Assign a clean vacant room and post the first night&apos;s room charge.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label>Reservation</Label>
            <Select
              value={selected?.id ?? ""}
              onValueChange={(id) =>
                onSelectReservation(arrivals.find((r) => r.id === id) ?? null)
              }
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Select arrival" />
              </SelectTrigger>
              <SelectContent>
                {arrivals.map((r) => (
                  <SelectItem key={r.id} value={r.id}>
                    {guestName(r)} · {r.check_in} · {r.room_type_name}
                    {r.status === "pre_checked_in" ? " · Pre-checked in" : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label>Room</Label>
            <Select value={roomId || undefined} onValueChange={onRoomChange}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Select vacant clean room" />
              </SelectTrigger>
              <SelectContent>
                {rooms.map((rm) => (
                  <SelectItem key={rm.id} value={rm.id}>
                    {rm.room_number} · {rm.room_type_name} · Clean
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {selected && rooms.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                No vacant rooms of this type available for these dates.
              </p>
            ) : null}
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={onSubmit} disabled={!selected || !roomId || pending}>
            {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : "Check in"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function AssignRoomDialog({
  open,
  onOpenChange,
  arrivals,
  rooms,
  reservations,
  selected,
  roomId,
  onSelectReservation,
  onRoomChange,
  onSubmit,
  pending,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  arrivals: Reservation[];
  rooms: HotelRoom[];
  reservations: Reservation[];
  selected: Reservation | null;
  roomId: string;
  onSelectReservation: (res: Reservation | null) => void;
  onRoomChange: (id: string) => void;
  onSubmit: () => void;
  pending: boolean;
}) {
  const options = useMemo(() => {
    if (!selected) return [];
    return rooms.filter(
      (rm) =>
        rm.room_type_id === selected.room_type_id &&
        isRoomAssignable({
          room: rm,
          checkIn: selected.check_in,
          checkOut: selected.check_out,
          reservations,
          excludeReservationId: selected.id,
        }),
    );
  }, [rooms, selected, reservations]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Assign room</DialogTitle>
          <DialogDescription>
            Pre-assign a room to a reserved arrival. Same-day stays need a clean
            vacant room; dirty rooms can only be assigned after the cleaning day.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label>Reservation</Label>
            <Select
              value={selected?.id ?? ""}
              onValueChange={(id) =>
                onSelectReservation(arrivals.find((r) => r.id === id) ?? null)
              }
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Select reserved stay" />
              </SelectTrigger>
              <SelectContent>
                {arrivals.map((r) => (
                  <SelectItem key={r.id} value={r.id}>
                    {guestName(r)} · {r.check_in} · {r.room_type_name}
                    {r.room_number ? ` · currently ${r.room_number}` : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label>Room</Label>
            <Select value={roomId || undefined} onValueChange={onRoomChange}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Select room" />
              </SelectTrigger>
              <SelectContent>
                {options.map((rm) => (
                  <SelectItem key={rm.id} value={rm.id}>
                    {rm.room_number} · {rm.room_type_name} · {ROOM_STATUS_LABEL[rm.status]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {selected && options.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                No vacant rooms of this type available for these dates.
              </p>
            ) : null}
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={onSubmit} disabled={!selected || !roomId || pending}>
            {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : "Assign room"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function CheckOutDialog({
  open,
  onOpenChange,
  inHouse,
  selected,
  currency,
  onSelectReservation,
  onSubmit,
  pending,
  onOpenBilling,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  inHouse: Reservation[];
  selected: Reservation | null;
  currency?: string;
  onSelectReservation: (res: Reservation | null) => void;
  onSubmit: () => void;
  pending: boolean;
  onOpenBilling?: () => void;
}) {
  const balance = Number(selected?.folio_balance ?? 0);
  const hasBalance = Number.isFinite(balance) && balance > 0;
  const pay = selected ? paymentStatus(selected) : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Check out guest</DialogTitle>
          <DialogDescription>
            End the stay, close the folio, and send the room to housekeeping.
            The folio balance must be settled first.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label>In-house guest</Label>
            <Select
              value={selected?.id ?? ""}
              onValueChange={(id) =>
                onSelectReservation(inHouse.find((r) => r.id === id) ?? null)
              }
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Select checked-in stay" />
              </SelectTrigger>
              <SelectContent>
                {inHouse.map((r) => (
                  <SelectItem key={r.id} value={r.id}>
                    {guestName(r)} · Rm {r.room_number || "—"} · {r.check_out}
                    {r.checkout_requested_at ? " · Checkout requested" : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {inHouse.length === 0 ? (
              <p className="text-xs text-muted-foreground">No guests are checked in right now.</p>
            ) : null}
          </div>
          {selected ? (
            <div className="rounded-lg border bg-muted/30 p-3 text-sm">
              <div className="flex items-center justify-between gap-2">
                <span className="text-muted-foreground">Room</span>
                <span className="font-medium">{selected.room_number || "—"}</span>
              </div>
              <div className="mt-1.5 flex items-center justify-between gap-2">
                <span className="text-muted-foreground">Stay</span>
                <span className="font-medium">
                  {selected.check_in} → {selected.check_out}
                </span>
              </div>
              <div className="mt-1.5 flex items-center justify-between gap-2">
                <span className="text-muted-foreground">Balance</span>
                <span className={cn("font-medium", hasBalance && "text-amber-700 dark:text-amber-400")}>
                  {formatMoney(selected.folio_balance, currency)}
                  {pay ? ` · ${pay.label}` : ""}
                </span>
              </div>
              {hasBalance ? (
                <p className="mt-2 text-xs text-amber-700 dark:text-amber-400">
                  Settle the folio in Billing before check-out.
                  {onOpenBilling ? (
                    <>
                      {" "}
                      <button
                        type="button"
                        className="underline underline-offset-2"
                        onClick={onOpenBilling}
                      >
                        Open Billing
                      </button>
                    </>
                  ) : null}
                </p>
              ) : (
                <p className="mt-2 text-xs text-muted-foreground">
                  Folio is clear — check-out will mark the room dirty for housekeeping.
                </p>
              )}
            </div>
          ) : null}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={onSubmit} disabled={!selected || hasBalance || pending}>
            {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : "Check out"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function CancelReservationDialog({
  open,
  onOpenChange,
  arrivals,
  selected,
  reason,
  onReasonChange,
  onSelectReservation,
  onSubmit,
  pending,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  arrivals: Reservation[];
  selected: Reservation | null;
  reason: string;
  onReasonChange: (value: string) => void;
  onSelectReservation: (res: Reservation | null) => void;
  onSubmit: () => void;
  pending: boolean;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Cancel reservation</DialogTitle>
          <DialogDescription>
            Cancel a booking before arrival. Releases any assigned room. Reserved and
            pre-checked-in stays can be cancelled — checked-in guests must check out
            instead.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label>Reservation</Label>
            <Select
              value={selected?.id ?? ""}
              onValueChange={(id) =>
                onSelectReservation(arrivals.find((r) => r.id === id) ?? null)
              }
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Select reserved stay" />
              </SelectTrigger>
              <SelectContent>
                {arrivals.map((r) => (
                  <SelectItem key={r.id} value={r.id}>
                    {guestName(r)} · {r.check_in} · {r.room_type_name}
                    {r.room_number ? ` · Rm ${r.room_number}` : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {arrivals.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                No reserved or pre-checked-in arrivals to cancel.
              </p>
            ) : null}
          </div>
          <div className="space-y-1.5">
            <Label>Reason (optional)</Label>
            <Input
              value={reason}
              onChange={(e) => onReasonChange(e.target.value)}
              placeholder="Guest request, dates changed…"
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Keep booking
          </Button>
          <Button variant="destructive" onClick={onSubmit} disabled={!selected || pending}>
            {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : "Cancel reservation"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function WalkInDialog({
  open,
  onOpenChange,
  form,
  onChange,
  roomTypes,
  rooms,
  reservations,
  hotelId,
  currency,
  onSubmit,
  pending,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  form: WalkInForm;
  onChange: (next: WalkInForm) => void;
  roomTypes: RoomType[];
  rooms: HotelRoom[];
  reservations: Reservation[];
  hotelId?: string;
  currency?: string;
  onSubmit: () => void;
  pending: boolean;
}) {
  const money = (value: unknown) => formatMoney(value, hotelCurrency(currency));
  const quoteQ = useQuery({
    queryKey: [
      "walk-in-rate-quote",
      hotelId,
      form.room_type_id,
      form.check_in,
      form.check_out,
    ],
    queryFn: () =>
      quoteStay({
        hotel_id: hotelId!,
        room_type_id: form.room_type_id,
        check_in: form.check_in,
        check_out: form.check_out,
      }),
    enabled:
      open &&
      Boolean(hotelId && form.room_type_id && form.check_in && form.check_out) &&
      form.check_out > form.check_in,
  });
  const matchingRooms = useMemo(
    () =>
      rooms.filter(
        (rm) =>
          rm.room_type_id === form.room_type_id &&
          isRoomAssignable({
            room: rm,
            checkIn: form.check_in,
            checkOut: form.check_out,
            reservations,
            immediate: form.check_in_now,
          }),
      ),
    [
      rooms,
      form.room_type_id,
      form.check_in,
      form.check_out,
      form.check_in_now,
      reservations,
    ],
  );

  const typeAvailable = useMemo(
    () =>
      Boolean(form.room_type_id) &&
      roomTypeHasAvailability({
        rooms,
        roomTypeId: form.room_type_id,
        checkIn: form.check_in,
        checkOut: form.check_out,
        reservations,
        immediate: form.check_in_now,
      }),
    [
      rooms,
      form.room_type_id,
      form.check_in,
      form.check_out,
      form.check_in_now,
      reservations,
    ],
  );

  function patch(next: Partial<WalkInForm>) {
    const merged = { ...form, ...next };
    const selected = rooms.find((r) => r.id === merged.room_id);
    if (
      merged.room_id &&
      (!selected ||
        !isRoomAssignable({
          room: selected,
          checkIn: merged.check_in,
          checkOut: merged.check_out,
          reservations,
          immediate: merged.check_in_now,
        }))
    ) {
      merged.room_id = "";
      if (merged.check_in_now) merged.check_in_now = false;
    }
    onChange(merged);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Walk-in reservation</DialogTitle>
          <DialogDescription>
            Create a guest and booking at the desk. Optionally check them in right away.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label>First name</Label>
            <Input
              value={form.first_name}
              onChange={(e) => patch({ first_name: e.target.value })}
            />
          </div>
          <div className="space-y-1.5">
            <Label>Last name</Label>
            <Input
              value={form.last_name}
              onChange={(e) => patch({ last_name: e.target.value })}
            />
          </div>
          <div className="space-y-1.5">
            <Label>Email</Label>
            <Input
              type="email"
              value={form.email}
              onChange={(e) => patch({ email: e.target.value })}
            />
          </div>
          <div className="space-y-1.5 sm:col-span-2">
            <Label>Phone</Label>
            <PhoneInput
              value={form.phone}
              onChange={(phone) => patch({ phone })}
              placeholder="3421234567"
            />
          </div>
          <div className="space-y-1.5 sm:col-span-2">
            <Label>Room type</Label>
            <Select
              value={form.room_type_id}
              onValueChange={(room_type_id) => patch({ room_type_id, room_id: "" })}
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Select type" />
              </SelectTrigger>
              <SelectContent>
                {roomTypes.map((rt) => (
                  <SelectItem key={rt.id} value={rt.id}>
                    {rt.name} · base {money(rt.base_rate)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {quoteQ.data ? (
              <p className="text-xs text-muted-foreground">
                Stay quote: {money(quoteQ.data.average_nightly)}/night avg · total{" "}
                {money(quoteQ.data.total)} ({quoteQ.data.nights} night
                {quoteQ.data.nights === 1 ? "" : "s"}). Nightly rate follows the calendar when charged.
              </p>
            ) : null}
          </div>
          <div className="space-y-1.5 sm:col-span-2">
            <Label>{form.check_in_now ? "Room (required for check-in)" : "Room (optional)"}</Label>
            <Select
              value={form.room_id || undefined}
              onValueChange={(v) => {
                const room_id = v === "__none__" ? "" : v;
                patch({
                  room_id,
                  check_in_now: room_id ? form.check_in_now : false,
                });
              }}
            >
              <SelectTrigger className="w-full">
                <SelectValue
                  placeholder={
                    form.check_in_now ? "Select a room" : "Select a room (optional)"
                  }
                />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__none__" disabled={form.check_in_now}>
                  Assign later
                </SelectItem>
                {matchingRooms.map((rm) => (
                  <SelectItem key={rm.id} value={rm.id}>
                    {rm.room_number} · {ROOM_STATUS_LABEL[rm.status]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {form.check_in_now && !form.room_id ? (
              <p className="text-xs text-amber-700 dark:text-amber-400">
                Pick a vacant clean room to check the guest in now, or uncheck
                &ldquo;Check in now&rdquo;.
              </p>
            ) : null}
            {form.room_type_id && matchingRooms.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                {typeAvailable
                  ? "No vacant rooms of this type ready to assign yet. You can still book and assign later."
                  : "No rooms of this type are available for these dates (cleaning schedule, maintenance, or existing bookings)."}
              </p>
            ) : null}
          </div>
          <div className="space-y-1.5">
            <Label>Check in</Label>
            <Input
              type="date"
              value={form.check_in}
              onChange={(e) => patch({ check_in: e.target.value })}
            />
          </div>
          <div className="space-y-1.5">
            <Label>Check out</Label>
            <Input
              type="date"
              value={form.check_out}
              onChange={(e) => patch({ check_out: e.target.value })}
            />
          </div>
          <div className="space-y-1.5">
            <Label>Adults</Label>
            <Input
              type="number"
              min={1}
              value={form.adults}
              onChange={(e) => patch({ adults: e.target.value })}
            />
          </div>
          <label className="flex items-center gap-2 self-end pb-2 text-sm">
            <input
              type="checkbox"
              className="size-4 rounded border"
              checked={form.check_in_now}
              onChange={(e) => patch({ check_in_now: e.target.checked })}
            />
            Check in now
          </label>
        </div>
        <DialogFooter className="flex-col items-stretch gap-2 sm:flex-row sm:items-center">
          {form.check_in_now && !form.room_id ? (
            <p className="flex-1 text-xs text-amber-700 dark:text-amber-400 sm:mr-auto">
              Select a clean vacant room before creating a check-in booking.
            </p>
          ) : form.room_type_id && !typeAvailable ? (
            <p className="flex-1 text-xs text-amber-700 dark:text-amber-400 sm:mr-auto">
              Choose later dates or another room type — nothing is available for this stay.
            </p>
          ) : null}
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={onSubmit}
            disabled={
              pending ||
              !form.first_name.trim() ||
              !form.last_name.trim() ||
              !form.room_type_id ||
              !form.check_in ||
              !form.check_out ||
              !typeAvailable ||
              (form.check_in_now && !form.room_id)
            }
          >
            {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : "Create booking"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function ReservationDetailDialog({
  open,
  onOpenChange,
  reservation,
  currency,
  canCheckIn,
  canCheckOut,
  canCancel,
  canAssign,
  onCheckIn,
  onCheckOut,
  onCancel,
  onAssign,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  reservation: Reservation | null;
  currency?: string;
  canCheckIn: boolean;
  canCheckOut: boolean;
  canCancel: boolean;
  canAssign: boolean;
  onCheckIn: () => void;
  onCheckOut: () => void;
  onCancel: () => void;
  onAssign: () => void;
}) {
  const [docs, setDocs] = useState<
    Array<{ id: string; doc_type: string; file_name?: string }>
  >([]);
  const [portalUrl, setPortalUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !reservation?.id) {
      setDocs([]);
      setPortalUrl(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const data = await fetchApi(
          `/hotel/reservations/${reservation.id}/guest-documents`,
        );
        if (cancelled) return;
        setDocs((data.documents as typeof docs) || []);
        setPortalUrl((data.portal_url as string) || null);
      } catch {
        if (!cancelled) {
          setDocs([]);
          setPortalUrl(null);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, reservation?.id]);

  if (!reservation) return null;
  const pay = paymentStatus(reservation);
  const toneClass =
    pay.tone === "due"
      ? "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-100"
      : pay.tone === "paid"
        ? "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-100"
        : pay.tone === "closed"
          ? "bg-muted text-muted-foreground"
          : "bg-muted text-muted-foreground";
  const preArrival = isPreArrivalStatus(reservation.status);
  const token = getAccessToken();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{guestName(reservation)}</DialogTitle>
          <DialogDescription>
            {reservation.room_type_name}
            {reservation.room_number ? ` · Room ${reservation.room_number}` : " · Unassigned"}
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3 text-sm sm:grid-cols-2">
          <div>
            <p className="text-xs text-muted-foreground">Stay</p>
            <p className="font-medium">
              {reservation.check_in} → {reservation.check_out}
            </p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Status</p>
            <p className="font-medium">
              {reservationStatusLabel(reservation.status)}
              {reservation.checkout_requested_at ? (
                <Badge className="ml-2" variant="secondary">
                  Checkout requested
                </Badge>
              ) : null}
            </p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Email</p>
            <p className="font-medium">{reservation.guest_email || "—"}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Nightly rate</p>
            <p className="font-medium">{formatMoney(reservation.rate, currency)}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Balance due</p>
            <p className="font-medium">{formatMoney(reservation.folio_balance, currency)}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Payment</p>
            <Badge className={cn(toneClass)} variant="secondary">
              {pay.label}
            </Badge>
          </div>
        </div>

        {(docs.length > 0 || portalUrl || reservation.status === "pre_checked_in") && (
          <div className="rounded-md border bg-muted/30 p-3 text-sm">
            <p className="text-xs font-medium text-muted-foreground">Guest portal</p>
            {reservation.status === "pre_checked_in" ? (
              <p className="mt-1 text-violet-700 dark:text-violet-300">
                Guest completed pre-check-in
                {reservation.registration_signed_at ? " · registration signed" : ""}.
              </p>
            ) : null}
            {docs.length > 0 ? (
              <ul className="mt-2 space-y-1">
                {docs.map((d) => (
                  <li key={d.id}>
                    <a
                      className="underline"
                      href="#"
                      onClick={(e) => {
                        e.preventDefault();
                        void (async () => {
                          const res = await fetch(
                            `${API_BASE}/hotel/guest-documents/${d.id}`,
                            {
                              headers: token ? { Authorization: `Bearer ${token}` } : {},
                              credentials: "include",
                            },
                          );
                          if (!res.ok) return;
                          const blob = await res.blob();
                          const url = URL.createObjectURL(blob);
                          window.open(url, "_blank");
                        })();
                      }}
                    >
                      {d.doc_type.replace(/_/g, " ")}
                      {d.file_name ? ` · ${d.file_name}` : ""}
                    </a>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-1 text-xs text-muted-foreground">No ID documents uploaded yet.</p>
            )}
            {portalUrl ? (
              <p className="mt-2 truncate text-xs text-muted-foreground">
                Portal: {portalUrl}
              </p>
            ) : null}
          </div>
        )}

        <DialogFooter className="gap-2 sm:gap-0">
          {preArrival && canCancel ? (
            <Button variant="destructive" onClick={onCancel}>
              Cancel booking
            </Button>
          ) : null}
          {preArrival && canAssign ? (
            <Button variant="outline" onClick={onAssign}>
              Assign room
            </Button>
          ) : null}
          {preArrival && canCheckIn ? (
            <Button onClick={onCheckIn}>Check in</Button>
          ) : null}
          {reservation.status === "checked_in" && canCheckOut ? (
            <Button onClick={onCheckOut}>Check out</Button>
          ) : null}
          <Button variant="secondary" onClick={() => onOpenChange(false)}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
