import { describe, it, expect } from "vitest";
import { hasPermission, useHotelPermissions } from "@/lib/rbac";
import type { User } from "@/lib/auth";

function user(overrides: Partial<User> = {}): User {
  return {
    user_id: "u1",
    email: "desk@hotel.test",
    full_name: "Desk",
    tenant_id: "hotel-1",
    is_superadmin: false,
    roles: [{ role_name: "Front Desk" }],
    permissions: [
      "bookings.read",
      "bookings.write",
      "guests.read",
      "guests.write",
      "reservations.check_in",
      "reservations.check_out",
      "rooms.read",
      "folios.read",
      "folios.write",
    ],
    ...overrides,
  };
}

describe("hotel RBAC helpers", () => {
  it("hides ledger reports from Front Desk", () => {
    const desk = user();
    expect(hasPermission(desk, "ledger.read")).toBe(false);
    expect(hasPermission(desk, "folios.read")).toBe(true);
    const perms = useHotelPermissions(desk);
    expect(perms.canSeeLedger).toBe(false);
    expect(perms.canSeeFolios).toBe(true);
    expect(perms.canToggleRoomStatus).toBe(false);
  });

  it("restricts room status toggles to housekeeping / manager grants", () => {
    const desk = user();
    expect(hasPermission(desk, "rooms.update_status")).toBe(false);
    const hk = user({
      roles: [{ role_name: "Housekeeping" }],
      permissions: ["rooms.read", "rooms.update_status"],
    });
    expect(hasPermission(hk, "rooms.update_status")).toBe(true);
    expect(hasPermission(hk, "bookings.read")).toBe(false);
    expect(hasPermission(hk, "folios.read")).toBe(false);
    expect(hasPermission(hk, "rooms.write")).toBe(false);
    const perms = useHotelPermissions(hk);
    expect(perms.canToggleRoomStatus).toBe(true);
    expect(perms.canSeeFolios).toBe(false);
    expect(perms.canManageRooms).toBe(false);
    expect(perms.canSeeFrontDesk).toBe(false);
  });

  it("lets hotel.manage exercise every hotel permission except platform.admin", () => {
    const owner = user({
      roles: [{ role_name: "Hotel Owner" }],
      permissions: ["hotel.manage"],
    });
    expect(hasPermission(owner, "rooms.update_status")).toBe(true);
    expect(hasPermission(owner, "ledger.read")).toBe(true);
    expect(hasPermission(owner, "rooms.write")).toBe(true);
    expect(hasPermission(owner, "platform.admin")).toBe(false);
    expect(useHotelPermissions(owner).canManageStaff).toBe(true);
    expect(useHotelPermissions(owner).canManageHotel).toBe(true);
    expect(useHotelPermissions(owner).canGenerateReports).toBe(true);
  });

  it("hides staff management from front desk", () => {
    const desk = user();
    expect(useHotelPermissions(desk).canManageStaff).toBe(false);
  });

  it("lets general manager manage staff and reports but not hotel settings", () => {
    const gm = user({
      roles: [{ role_name: "General Manager" }],
      permissions: [
        "hotel.staff.manage",
        "reports.generate",
        "bookings.read",
        "bookings.write",
        "guests.read",
        "guests.write",
        "reservations.check_in",
        "reservations.check_out",
        "rooms.read",
        "rooms.write",
        "rooms.update_status",
        "folios.read",
        "folios.write",
        "ledger.read",
        "invoices.read",
      ],
    });
    const perms = useHotelPermissions(gm);
    expect(perms.canManageStaff).toBe(true);
    expect(perms.canManageHotel).toBe(false);
    expect(perms.canGenerateReports).toBe(true);
    expect(perms.canSeeFinance).toBe(false);
  });

  it("lets branch manager manage staff and reports for their property", () => {
    const mgr = user({
      roles: [{ role_name: "Hotel Manager" }],
      permissions: [
        "hotel.staff.manage",
        "reports.generate",
        "bookings.read",
        "rooms.write",
        "folios.read",
        "ledger.read",
        "invoices.read",
      ],
    });
    const perms = useHotelPermissions(mgr);
    expect(perms.canManageStaff).toBe(true);
    expect(perms.canManageHotel).toBe(false);
    expect(perms.canGenerateReports).toBe(true);
  });

  it("superadmin bypasses tenant permission checks", () => {
    const admin = user({
      is_superadmin: true,
      tenant_id: null,
      permissions: ["platform.admin"],
    });
    expect(hasPermission(admin, "ledger.read")).toBe(true);
    expect(hasPermission(admin, "rooms.update_status")).toBe(true);
  });

  it("lets accountants into finance hub but not front desk", () => {
    const acc = user({
      roles: [{ role_name: "Accountant" }],
      permissions: [
        "folios.read",
        "ledger.read",
        "ledger.write",
        "invoices.read",
        "invoices.write",
        "finance.night_audit",
        "finance.tax_reports",
        "finance.refunds",
      ],
    });
    const perms = useHotelPermissions(acc);
    expect(perms.canSeeFinance).toBe(true);
    expect(perms.canSeeFrontDesk).toBe(false);
    expect(perms.canCheckIn).toBe(false);
    expect(perms.canWriteFolios).toBe(false);
    expect(perms.canRunNightAudit).toBe(true);
    expect(perms.canApproveRefunds).toBe(true);
  });

  it("keeps front desk folio settlement without ledger write", () => {
    const desk = user();
    const perms = useHotelPermissions(desk);
    expect(perms.canWriteFolios).toBe(true);
    expect(perms.canSeeFinance).toBe(false);
    expect(perms.canWriteLedger).toBe(false);
  });
});
