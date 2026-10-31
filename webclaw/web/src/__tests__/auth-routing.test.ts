import { describe, expect, it } from "vitest";
import type { User } from "@/lib/auth";
import { postLoginPath, roleLabel } from "@/lib/auth-routing";

function user(overrides: Partial<User> = {}): User {
  return {
    user_id: "u1",
    email: "ada@hotel.test",
    full_name: "Ada",
    ...overrides,
  };
}

describe("postLoginPath", () => {
  it("sends a super admin to the platform dashboard", () => {
    expect(
      postLoginPath(user({ is_superadmin: true, role: "SUPER_ADMIN", tenant_id: null })),
    ).toBe("/superadmin/dashboard");
  });

  it("sends a hotel user to the property dashboard", () => {
    expect(
      postLoginPath(
        user({
          tenant_id: "hotel-1",
          role: "FRONT_DESK",
          roles: [{ role_name: "Front Desk" }],
          permissions: ["bookings.read", "rooms.read"],
        }),
      ),
    ).toBe("/app/hotel/dashboard");
  });

  it("sends housekeeping-only staff to the housekeeping portal", () => {
    expect(
      postLoginPath(
        user({
          tenant_id: "hotel-1",
          role: "HOUSEKEEPING",
          roles: [{ role_name: "Housekeeping" }],
          permissions: ["rooms.read", "rooms.update_status"],
        }),
      ),
    ).toBe("/app/hotel/housekeeping");
  });

  it("keeps an impersonated super admin inside the hotel workspace", () => {
    expect(
      postLoginPath(
        user({
          is_superadmin: false,
          is_impersonating: true,
          tenant_id: "hotel-9",
          role: "HOTEL_OWNER",
        }),
      ),
    ).toBe("/app/hotel/dashboard");
  });

  it("sends an accountant to the finance hub", () => {
    expect(
      postLoginPath(
        user({
          role: "ACCOUNTANT",
          tenant_id: "hotel-1",
          permissions: [
            "folios.read",
            "ledger.read",
            "ledger.write",
            "finance.night_audit",
          ],
        }),
      ),
    ).toBe("/app/hotel/finance");
  });
});

describe("roleLabel", () => {
  it("labels a regional manager", () => {
    expect(roleLabel("REGIONAL_MANAGER")).toBe("Regional Manager");
  });
});
