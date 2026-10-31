import type { User } from "./auth";
import { hasPermission } from "./rbac";

/** Where a signed-in user belongs after login or when they hit a shared URL. */
export function postLoginPath(user: User | null | undefined): string {
  if (!user) return "/login";
  if (user.is_impersonating) return "/app/hotel/dashboard";
  if (user.is_superadmin || user.role === "SUPER_ADMIN") return "/superadmin/dashboard";
  const roleNames = user.roles?.map((r) => r.role_name) ?? [];
  if (roleNames.includes("System Manager") || roleNames.includes("SUPER_ADMIN")) {
    return "/superadmin/dashboard";
  }
  if (
    user.partner_id ||
    roleNames.includes("Partner Admin") ||
    roleNames.includes("PARTNER_ADMIN") ||
    (user.permissions || []).includes("partner.console.view")
  ) {
    return "/partner";
  }
  if (
    roleNames.some((n) =>
      [
        "Platform Support",
        "Platform Billing",
        "Platform Security",
        "PLATFORM_SUPPORT",
        "PLATFORM_BILLING",
        "PLATFORM_SECURITY",
      ].includes(n),
    ) ||
    (user.permissions || []).some((p) => p.startsWith("platform."))
  ) {
    return "/superadmin/dashboard";
  }
  // Accountant / finance staff land on the finance hub, not front desk.
  if (
    (hasPermission(user, "ledger.write") || hasPermission(user, "finance.night_audit")) &&
    !hasPermission(user, "bookings.read") &&
    !hasPermission(user, "hotel.manage")
  ) {
    return "/app/hotel/finance";
  }
  // Housekeeping-only staff land on the mobile task board, not billing/desk.
  if (
    hasPermission(user, "rooms.update_status") &&
    !hasPermission(user, "bookings.read") &&
    !hasPermission(user, "hotel.manage")
  ) {
    return "/app/hotel/housekeeping";
  }
  if (user.tenant_id || (user.tenants && user.tenants.length > 0)) {
    return "/app/hotel/dashboard";
  }
  if (user.role && user.role !== "SUPER_ADMIN") return "/app/hotel/dashboard";
  return "/dashboard";
}

const ROLE_LABELS: Record<string, string> = {
  SUPER_ADMIN: "Super Admin",
  HOTEL_OWNER: "Hotel Owner",
  HOTEL_ADMIN: "Hotel Admin",
  GENERAL_MANAGER: "General Manager",
  HOTEL_MANAGER: "Hotel Manager",
  FRONT_DESK: "Front Desk",
  HOUSEKEEPING: "Housekeeping",
  HOTEL_ACCOUNTANT: "Hotel Accountant",
  ACCOUNTANT: "Branch Accountant",
  HOTEL_STAFF: "Hotel Staff",
  REGIONAL_MANAGER: "Regional Manager",
};

export function roleLabel(role: string | null | undefined): string {
  if (!role) return "";
  return ROLE_LABELS[role] || role.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
