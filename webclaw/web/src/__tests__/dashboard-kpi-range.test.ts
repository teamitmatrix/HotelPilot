import { describe, expect, it } from "vitest";
import { kpiRangeBounds } from "@/app/(authenticated)/dashboard/page";

describe("kpiRangeBounds", () => {
  const now = new Date(2026, 8, 30); // Sep 30, 2026 local

  it("defaults today to a one-day exclusive window", () => {
    expect(kpiRangeBounds("today", now)).toEqual({
      from: "2026-09-30",
      to: "2026-10-01",
    });
  });

  it("covers the last 7 inclusive days for 7d", () => {
    expect(kpiRangeBounds("7d", now)).toEqual({
      from: "2026-09-24",
      to: "2026-10-01",
    });
  });

  it("covers the last 30 inclusive days for 30d", () => {
    expect(kpiRangeBounds("30d", now)).toEqual({
      from: "2026-09-01",
      to: "2026-10-01",
    });
  });
});
