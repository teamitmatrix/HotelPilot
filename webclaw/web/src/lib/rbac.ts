import type { User } from "./auth";

/** Named permission held by Hotel Owner — UI treats this as all hotel ops. */
const HOTEL_ALL = "hotel.manage";

export function hasPermission(user: User | null | undefined, permission: string): boolean {
  if (!user) return false;
  if (user.is_superadmin && !user.is_impersonating) return true;
  const perms = user.permissions ?? [];
  if (perms.includes("platform.admin") && !user.is_impersonating) return true;
  if (perms.includes(HOTEL_ALL) && permission !== "platform.admin") return true;
  return perms.includes(permission);
}

export function useHotelPermissions(user: User | null | undefined) {
  const canSeeFinance =
    hasPermission(user, "ledger.write") ||
    hasPermission(user, "finance.night_audit") ||
    hasPermission(user, "finance.tax_reports") ||
    hasPermission(user, "finance.refunds") ||
    (hasPermission(user, "ledger.read") && hasPermission(user, "invoices.write"));

  const canManageHotel = hasPermission(user, "hotel.manage");
  const canManageStaff =
    canManageHotel || hasPermission(user, "hotel.staff.manage");
  const canGenerateReports =
    canManageHotel || hasPermission(user, "reports.generate");

  return {
    canSeeFrontDesk: hasPermission(user, "bookings.read"),
    canWriteBookings: hasPermission(user, "bookings.write"),
    canCheckIn: hasPermission(user, "reservations.check_in"),
    canCheckOut: hasPermission(user, "reservations.check_out"),
    canSeeRooms: hasPermission(user, "rooms.read"),
    canManageRooms: hasPermission(user, "rooms.write"),
    canToggleRoomStatus: hasPermission(user, "rooms.update_status"),
    canSeeFolios: hasPermission(user, "folios.read"),
    canWriteFolios: hasPermission(user, "folios.write"),
    canSeeLedger: hasPermission(user, "ledger.read"),
    canWriteLedger: hasPermission(user, "ledger.write"),
    canSeeInvoices: hasPermission(user, "invoices.read"),
    canWriteInvoices: hasPermission(user, "invoices.write"),
    canRunNightAudit: hasPermission(user, "finance.night_audit"),
    canSeeTaxReports: hasPermission(user, "finance.tax_reports"),
    canApproveRefunds: hasPermission(user, "finance.refunds"),
    /** Finance & Accounting hub — Owner / Admin / Accountants. */
    canSeeFinance,
    /** Owner / Admin — property settings, branches, billing. */
    canManageHotel,
    /** Owner / Admin / GM / Branch Manager — staff invite within allowed roles. */
    canManageStaff,
    /** Owner / Admin / GM / Branch Manager — ops & finance reports. */
    canGenerateReports,
  };
}

export type HotelPermissions = ReturnType<typeof useHotelPermissions>;
