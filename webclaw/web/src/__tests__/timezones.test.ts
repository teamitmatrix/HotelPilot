import { describe, it, expect } from "vitest";
import { TIMEZONES, filterTimezones, timezoneLabel } from "@/lib/timezones";

describe("timezone list", () => {
  it("includes every previously listed registration timezone plus a full IANA set", () => {
    for (const tz of [
      "UTC",
      "America/New_York",
      "America/Chicago",
      "America/Los_Angeles",
      "Europe/London",
      "Europe/Paris",
      "Asia/Dubai",
      "Asia/Riyadh",
      "Asia/Kolkata",
      "Asia/Singapore",
      "Australia/Sydney",
    ]) {
      expect(TIMEZONES).toContain(tz);
    }
    expect(TIMEZONES.length).toBeGreaterThan(50);
    expect(new Set(TIMEZONES).size).toBe(TIMEZONES.length);
  });

  it("filters by zone id, city, or offset label", () => {
    expect(filterTimezones("dubai")).toContain("Asia/Dubai");
    expect(filterTimezones("new york")).toContain("America/New_York");
    expect(filterTimezones("   ")).toEqual(TIMEZONES);
    expect(timezoneLabel("UTC")).toMatch(/UTC/);
  });
});
