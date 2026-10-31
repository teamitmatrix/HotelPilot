import { describe, it, expect } from "vitest";
import { COUNTRIES, filterCountries } from "@/lib/countries";

describe("country list", () => {
  it("includes every previously listed registration country plus a full ISO set", () => {
    for (const name of [
      "United States",
      "United Kingdom",
      "United Arab Emirates",
      "Saudi Arabia",
      "India",
      "Singapore",
      "Canada",
      "Australia",
      "Germany",
      "France",
      "Pakistan",
      "Nigeria",
      "Japan",
      "Brazil",
    ]) {
      expect(COUNTRIES).toContain(name);
    }
    expect(COUNTRIES.length).toBeGreaterThan(190);
    expect(new Set(COUNTRIES).size).toBe(COUNTRIES.length);
  });

  it("filters by case-insensitive substring", () => {
    const hits = filterCountries("stan");
    expect(hits).toContain("Pakistan");
    expect(hits).toContain("Afghanistan");
    expect(hits.every((c) => c.toLowerCase().includes("stan"))).toBe(true);
    expect(filterCountries("   ")).toEqual(COUNTRIES);
  });
});
