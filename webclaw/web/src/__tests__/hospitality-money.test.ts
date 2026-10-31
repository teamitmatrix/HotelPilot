import { describe, it, expect } from "vitest";
import { formatMoney, hotelCurrency } from "@/lib/hospitality";

describe("hotel currency", () => {
  it("normalizes ISO codes and falls back to USD", () => {
    expect(hotelCurrency("pkr")).toBe("PKR");
    expect(hotelCurrency("AED")).toBe("AED");
    expect(hotelCurrency("")).toBe("USD");
    expect(hotelCurrency("dollar")).toBe("USD");
  });

  it("formats room rates in the hotel's registration currency", () => {
    expect(formatMoney("100.00", "PKR")).toMatch(/PKR|Rs/);
    expect(formatMoney("100.00", "PKR")).not.toMatch(/\$100/);
    expect(formatMoney("180", "AED")).toMatch(/AED|Dhs|د\.إ/);
    expect(formatMoney("100.00", "USD")).toMatch(/\$100/);
  });
});
