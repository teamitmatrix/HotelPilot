import { describe, it, expect } from "vitest";
import { CURRENCIES, filterCurrencies } from "@/lib/currencies";

describe("currency list", () => {
  it("includes every previously listed registration currency plus a full ISO set", () => {
    const codes = CURRENCIES.map((c) => c.code);
    for (const code of ["USD", "EUR", "GBP", "AED", "SAR", "INR", "SGD", "CAD", "AUD", "PKR", "JPY", "NGN"]) {
      expect(codes).toContain(code);
    }
    expect(CURRENCIES.length).toBeGreaterThan(140);
    expect(new Set(codes).size).toBe(CURRENCIES.length);
    expect(codes.every((code) => /^[A-Z]{3}$/.test(code))).toBe(true);
  });

  it("filters by code or name", () => {
    const rupees = filterCurrencies("rupee");
    expect(rupees.map((c) => c.code)).toEqual(expect.arrayContaining(["INR", "PKR", "LKR"]));
    expect(filterCurrencies("usd").map((c) => c.code)).toEqual(["USD"]);
    expect(filterCurrencies("   ")).toEqual(CURRENCIES);
  });
});
