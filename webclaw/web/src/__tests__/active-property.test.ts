import { describe, expect, it } from "vitest";
import {
  PROPERTY_ALL,
  isPortfolioScope,
} from "@/lib/active-tenant";

describe("property scope helpers", () => {
  it("treats ALL as portfolio scope", () => {
    expect(isPortfolioScope(PROPERTY_ALL)).toBe(true);
    expect(isPortfolioScope("all")).toBe(true);
    expect(isPortfolioScope("prop-1")).toBe(false);
    expect(isPortfolioScope(null)).toBe(false);
  });
});
