import { describe, expect, it } from "vitest";
import { hospitalityclawAppPath } from "@/lib/hotel-route-map";

describe("hospitalityclawAppPath", () => {
  it("maps skill hotel/profile routes to hotel settings", () => {
    expect(hospitalityclawAppPath("/skills/hospitalityclaw/hotels")).toBe(
      "/app/hotel/settings?tab=property",
    );
  });

  it("maps rooms and rates to inventory", () => {
    expect(hospitalityclawAppPath("/skills/hospitalityclaw/rooms")).toBe("/app/hotel/inventory");
    expect(hospitalityclawAppPath("/skills/hospitalityclaw/rate-plans")).toBe(
      "/app/hotel/inventory",
    );
  });

  it("ignores non-hospitality skills", () => {
    expect(hospitalityclawAppPath("/skills/erpclaw/accounts")).toBeNull();
  });
});
