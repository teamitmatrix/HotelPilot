export const SKILL = "hospitalityclaw";

export interface Hotel {
  id: string;
  name: string;
  currency?: string;
  timezone?: string;
  country?: string;
  address?: string | null;
  phone?: string | null;
  email?: string | null;
  settings_json?: string | null;
}

export type HotelSettings = {
  check_in_time?: string;
  check_out_time?: string;
  cancellation_policy?: string;
  payment_gateway?: string;
  payment_publishable_key?: string;
  payment_secret_key?: string;
  payment_webhook_secret?: string;
  [key: string]: string | undefined;
};

export function parseHotelSettings(raw: string | null | undefined): HotelSettings {
  if (!raw) return {};
  try {
    const parsed = typeof raw === "string" ? JSON.parse(raw) : raw;
    return parsed && typeof parsed === "object" ? (parsed as HotelSettings) : {};
  } catch {
    return {};
  }
}

export type RoomStatus = "vacant" | "occupied" | "dirty" | "out_of_order";
export type ReservationStatus =
  | "reserved"
  | "pre_checked_in"
  | "checked_in"
  | "checked_out"
  | "cancelled"
  | "no_show";
export type FolioStatus = "open" | "closed" | "cancelled";

export interface HotelRoom {
  id: string;
  hotel_id: string;
  room_type_id: string;
  room_number: string;
  floor?: string | null;
  status: RoomStatus;
  /** Earliest bookable check-in after dirty cleaning (ISO date). Ignored for maintenance. */
  available_from?: string | null;
  room_type_name?: string;
  room_type_code?: string;
  room_type_rate?: string;
}

export interface RoomType {
  id: string;
  hotel_id: string;
  name: string;
  code: string;
  base_rate: string;
  max_occupancy?: number;
  description?: string | null;
  hotel_name?: string;
}

export interface Reservation {
  id: string;
  hotel_id: string;
  guest_id: string;
  room_id?: string | null;
  room_type_id: string;
  check_in: string;
  check_out: string;
  adults?: number;
  children?: number;
  status: ReservationStatus;
  rate: string;
  notes?: string | null;
  booking_token?: string | null;
  pre_check_in_at?: string | null;
  registration_signed_at?: string | null;
  checkout_requested_at?: string | null;
  first_name?: string;
  last_name?: string;
  guest_email?: string;
  room_type_name?: string;
  room_number?: string | null;
  folio_id?: string | null;
  folio_balance?: string | null;
  folio_status?: FolioStatus | null;
}

export function reservationStatusLabel(status: string): string {
  if (status === "pre_checked_in") return "Pre-checked in";
  return status.replace(/_/g, " ");
}

export function isPreArrivalStatus(status: string): boolean {
  return status === "reserved" || status === "pre_checked_in";
}

export interface Folio {
  id: string;
  reservation_id: string;
  guest_id: string;
  status: FolioStatus;
  balance: string;
  first_name?: string;
  last_name?: string;
  guest_email?: string;
  check_in?: string;
  check_out?: string;
  reservation_status?: ReservationStatus;
  room_rate?: string;
  room_number?: string | null;
}

export interface FolioCharge {
  id: string;
  folio_id: string;
  description: string;
  amount: string;
  charge_type: string;
  posting_date: string;
}

export interface RevenuePayment {
  id: string;
  amount: string;
  payment_method: string;
  status: string;
  posted_at?: string | null;
  reference?: string | null;
  description?: string;
}

export interface RevenueBooking {
  reservation_id: string;
  reservation_status: ReservationStatus;
  check_in: string;
  check_out: string;
  room_rate: string;
  first_name?: string;
  last_name?: string;
  guest_email?: string | null;
  room_type_name?: string;
  room_number?: string | null;
  folio_id?: string | null;
  folio_status?: FolioStatus | null;
  folio_balance: string;
  charges_total: string;
  payments_total: string;
  payments: RevenuePayment[];
}

export interface LedgerEntry {
  id: string;
  folio_id: string;
  debit: string;
  credit: string;
  description: string;
  posting_date: string;
  account_code?: string | null;
}

export interface RatePlan {
  id: string;
  hotel_id: string;
  room_type_id?: string | null;
  name: string;
  code: string;
  start_date: string;
  end_date: string;
  nightly_rate: string;
  priority?: number;
  is_active?: number | boolean;
  notes?: string | null;
  room_type_name?: string | null;
  room_type_code?: string | null;
}

export interface TaxRule {
  id: string;
  hotel_id: string;
  name: string;
  rate_percent: string;
  applies_to: "room" | "fnb" | "all" | string;
  erp_tax_template_id?: string | null;
  is_default?: number | boolean;
  is_active?: number | boolean;
}

export interface FolioBreakdown {
  room?: string;
  tax?: string;
  fnb?: string;
  other?: string;
  payment?: string;
  [key: string]: string | undefined;
}

export const ROOM_STATUS_LABEL: Record<RoomStatus, string> = {
  vacant: "Clean",
  occupied: "Occupied",
  dirty: "Dirty",
  out_of_order: "Maintenance",
};

/** Front-desk board labels (Vacant Clean / Vacant Dirty / …). */
export const BOARD_STATUS_LABEL: Record<RoomStatus, string> = {
  vacant: "Vacant Clean",
  dirty: "Vacant Dirty",
  occupied: "Occupied",
  out_of_order: "Out of Service",
};

export const BOARD_STATUS_FILTERS: { value: RoomStatus | "all"; label: string }[] = [
  { value: "all", label: "All rooms" },
  { value: "vacant", label: "Vacant Clean" },
  { value: "dirty", label: "Vacant Dirty" },
  { value: "occupied", label: "Occupied" },
  { value: "out_of_order", label: "Out of Service" },
];

export const ROOM_STATUS_CLASS: Record<RoomStatus, string> = {
  vacant: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
  occupied: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-200",
  dirty: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-200",
  out_of_order: "border border-red-200 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-200",
};

/** High-contrast badges for housekeeping cards (outdoor / dim-screen readability). */
export const HOUSEKEEPING_STATUS_BADGE: Record<RoomStatus, string> = {
  vacant:
    "border border-emerald-700/30 bg-emerald-600 font-semibold text-white shadow-sm dark:bg-emerald-500",
  occupied:
    "border border-blue-700/30 bg-blue-600 font-semibold text-white shadow-sm dark:bg-blue-500",
  dirty: "border border-red-800/40 bg-red-600 font-semibold text-white shadow-sm dark:bg-red-500",
  out_of_order:
    "border border-amber-900/40 bg-amber-500 font-semibold text-amber-950 shadow-sm dark:bg-amber-400 dark:text-amber-950",
};

export const HOUSEKEEPING_CARD_RING: Record<RoomStatus, string> = {
  vacant: "border-emerald-500/50 dark:border-emerald-600",
  occupied: "border-blue-500/40 dark:border-blue-700",
  dirty: "border-red-500/60 dark:border-red-700",
  out_of_order: "border-amber-500/70 dark:border-amber-600",
};

export const HOUSEKEEPING_STATUSES = [
  { value: "clean", label: "Clean" },
  { value: "dirty", label: "Dirty" },
  { value: "maintenance", label: "Maintenance" },
] as const;

/** Task-board filters for the Housekeeping portal. */
export type HousekeepingFilter =
  | "all"
  | "needs_cleaning"
  | "clean"
  | "in_progress"
  | "maintenance";

export const HOUSEKEEPING_FILTERS: {
  value: HousekeepingFilter;
  label: string;
  /** Compact label for narrow screens */
  shortLabel: string;
}[] = [
  { value: "all", label: "All rooms", shortLabel: "All" },
  { value: "needs_cleaning", label: "Dirty", shortLabel: "Dirty" },
  { value: "clean", label: "Clean & inspected", shortLabel: "Clean" },
  { value: "in_progress", label: "In progress", shortLabel: "In progress" },
  { value: "maintenance", label: "Maintenance", shortLabel: "Maint." },
];

export type HousekeepingFilterContext = {
  roomId?: string;
  inProgressRoomIds?: ReadonlySet<string>;
};

export function matchesHousekeepingFilter(
  status: RoomStatus,
  filter: HousekeepingFilter,
  ctx?: HousekeepingFilterContext,
): boolean {
  if (filter === "all") return true;
  if (filter === "needs_cleaning") return status === "dirty";
  if (filter === "clean") return status === "vacant";
  if (filter === "maintenance") return status === "out_of_order";
  if (filter === "in_progress") {
    return Boolean(ctx?.roomId && ctx.inProgressRoomIds?.has(ctx.roomId));
  }
  return true;
}

export type HousekeepingRoomStatusUpdate = {
  status: string;
  cleaning_date?: string;
  available_from?: string;
};

/** Primary one-tap action for a room on the housekeeping board. */
export function housekeepingPrimaryAction(
  status: RoomStatus,
): { status: string; label: string } | null {
  if (status === "dirty") return { status: "clean", label: "Mark Clean & Inspected" };
  if (status === "out_of_order") return { status: "clean", label: "Back in service" };
  if (status === "vacant") return { status: "dirty", label: "Mark Dirty" };
  return null;
}

export function paymentStatus(res: {
  folio_id?: string | null;
  folio_balance?: string | null;
  folio_status?: FolioStatus | null;
}): { label: string; tone: "paid" | "due" | "closed" | "none" } {
  if (!res.folio_id && res.folio_balance == null && !res.folio_status) {
    return { label: "No folio", tone: "none" };
  }
  if (res.folio_status === "cancelled") {
    return { label: "Cancelled", tone: "closed" };
  }
  if (res.folio_status === "closed") {
    return { label: "Settled", tone: "closed" };
  }
  const bal = Number(res.folio_balance || 0);
  if (Number.isNaN(bal) || bal <= 0) {
    return { label: "Paid", tone: "paid" };
  }
  return { label: "Balance due", tone: "due" };
}

export function nightCount(checkIn: string, checkOut: string): number {
  const a = parseISODate(checkIn);
  const b = parseISODate(checkOut);
  const ms = b.getTime() - a.getTime();
  return Math.max(1, Math.round(ms / 86_400_000));
}

export function guestName(row: { first_name?: string; last_name?: string }): string {
  return [row.first_name, row.last_name].filter(Boolean).join(" ") || "Guest";
}

export function hotelCurrency(value: unknown): string {
  const code = String(value ?? "").trim().toUpperCase();
  return /^[A-Z]{3}$/.test(code) ? code : "USD";
}

export function formatMoney(value: unknown, currency = "USD"): string {
  const num = typeof value === "string" ? parseFloat(value) : Number(value);
  if (Number.isNaN(num)) return String(value ?? "0");
  const code = hotelCurrency(currency);
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency: code }).format(num);
  } catch {
    return `${code} ${num.toFixed(2)}`;
  }
}

export function isoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function parseISODate(value: string): Date {
  const [y, m, d] = value.slice(0, 10).split("-").map(Number);
  return new Date(y, (m || 1) - 1, d || 1);
}

export function addDays(d: Date, n: number): Date {
  const next = new Date(d);
  next.setDate(next.getDate() + n);
  return next;
}

export function staysOnDate(res: Reservation, day: string): boolean {
  return res.check_in <= day && res.check_out > day && res.status !== "cancelled";
}

/** True when two half-open [checkIn, checkOut) stays overlap. */
export function staysOverlap(
  aIn: string,
  aOut: string,
  bIn: string,
  bOut: string,
): boolean {
  return aIn < bOut && aOut > bIn;
}

/**
 * Dirty rooms are assumed cleaned tomorrow, so the earliest assignable
 * check-in is the day after tomorrow. Same-day / on-the-spot stays require
 * a vacant (clean) room only.
 */
export function earliestDirtyCheckIn(today = isoDate(new Date())): string {
  return isoDate(addDays(parseISODate(today), 2));
}

/**
 * Earliest check-in for a room from housekeeping schedule.
 * Dirty without schedule defaults to day-after-tomorrow.
 * Maintenance is never bookable until marked clean.
 */
export function roomEarliestCheckIn(
  room: Pick<HotelRoom, "status" | "available_from">,
  today = isoDate(new Date()),
): string | null {
  if (room.status === "vacant" || room.status === "occupied") return today;
  if (room.status === "out_of_order") return null;
  const scheduled = room.available_from?.trim().slice(0, 10) || null;
  if (room.status === "dirty") {
    return scheduled || earliestDirtyCheckIn(today);
  }
  return null;
}

export function roomHasCalendarConflict(
  roomId: string,
  checkIn: string,
  checkOut: string,
  reservations: Reservation[],
  excludeReservationId?: string,
): boolean {
  return reservations.some(
    (r) =>
      r.room_id === roomId &&
      r.id !== excludeReservationId &&
      (r.status === "reserved" || r.status === "checked_in") &&
      staysOverlap(checkIn, checkOut, r.check_in, r.check_out),
  );
}

/**
 * Whether a physical room may cover a stay (assignment or inventory).
 * - Same-day / check-in-now: vacant (clean) only
 * - Dirty: check-in must be on/after available_from
 * - Maintenance: never bookable until marked clean
 * - Calendar overlaps always blocked
 */
export function isRoomAssignable(opts: {
  room: Pick<HotelRoom, "id" | "status" | "available_from">;
  checkIn: string;
  checkOut: string;
  reservations: Reservation[];
  today?: string;
  immediate?: boolean;
  excludeReservationId?: string;
}): boolean {
  const today = opts.today ?? isoDate(new Date());
  const { room, checkIn, checkOut, reservations, immediate, excludeReservationId } = opts;
  if (!checkIn || !checkOut || checkIn >= checkOut) return false;
  if (room.status === "out_of_order") return false;

  const sameDay = Boolean(immediate) || checkIn <= today;
  if (sameDay && room.status !== "vacant") return false;

  if (room.status === "dirty") {
    const earliest = roomEarliestCheckIn(room, today);
    if (!earliest || checkIn < earliest) return false;
  } else if (room.status !== "vacant" && room.status !== "occupied") {
    return false;
  }

  return !roomHasCalendarConflict(
    room.id,
    checkIn,
    checkOut,
    reservations,
    excludeReservationId,
  );
}

/** True when at least one room of this type can cover the stay (incl. assign-later). */
export function roomTypeHasAvailability(opts: {
  rooms: HotelRoom[];
  roomTypeId: string;
  checkIn: string;
  checkOut: string;
  reservations: Reservation[];
  today?: string;
  immediate?: boolean;
}): boolean {
  const ofType = opts.rooms.filter((r) => r.room_type_id === opts.roomTypeId);
  if (ofType.length === 0) return false;
  return ofType.some((room) =>
    isRoomAssignable({
      room,
      checkIn: opts.checkIn,
      checkOut: opts.checkOut,
      reservations: opts.reservations,
      today: opts.today,
      immediate: opts.immediate,
    }),
  );
}

export function itemsFrom<T>(data: Record<string, unknown> | undefined, key: string): T[] {
  if (!data) return [];
  const val = data[key];
  return Array.isArray(val) ? (val as T[]) : [];
}
