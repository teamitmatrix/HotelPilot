import { describe, expect, it } from "vitest";
import {
  BOARD_STATUS_LABEL,
  housekeepingPrimaryAction,
  isRoomAssignable,
  matchesHousekeepingFilter,
  nightCount,
  paymentStatus,
} from "@/lib/hospitality";

describe("front desk hospitality helpers", () => {
  it("labels board statuses for the room grid", () => {
    expect(BOARD_STATUS_LABEL.vacant).toBe("Vacant Clean");
    expect(BOARD_STATUS_LABEL.dirty).toBe("Vacant Dirty");
    expect(BOARD_STATUS_LABEL.occupied).toBe("Occupied");
    expect(BOARD_STATUS_LABEL.out_of_order).toBe("Out of Service");
  });

  it("counts nights for the tape chart bar width", () => {
    expect(nightCount("2026-09-01", "2026-09-03")).toBe(2);
    expect(nightCount("2026-09-01", "2026-09-02")).toBe(1);
  });

  it("classifies folio payment status", () => {
    expect(paymentStatus({ folio_balance: "0", folio_status: "open", folio_id: "f1" }).label).toBe(
      "Paid",
    );
    expect(paymentStatus({ folio_balance: "42.5", folio_status: "open", folio_id: "f1" }).tone).toBe(
      "due",
    );
    expect(paymentStatus({ folio_balance: "0", folio_status: "closed", folio_id: "f1" }).label).toBe(
      "Settled",
    );
  });
});

describe("housekeeping portal helpers", () => {
  it("filters dirty rooms into the needs-cleaning queue", () => {
    expect(matchesHousekeepingFilter("dirty", "needs_cleaning")).toBe(true);
    expect(matchesHousekeepingFilter("vacant", "needs_cleaning")).toBe(false);
    expect(matchesHousekeepingFilter("vacant", "clean")).toBe(true);
    expect(matchesHousekeepingFilter("out_of_order", "maintenance")).toBe(true);
    expect(
      matchesHousekeepingFilter("dirty", "in_progress", {
        roomId: "r1",
        inProgressRoomIds: new Set(["r1"]),
      }),
    ).toBe(true);
    expect(
      matchesHousekeepingFilter("dirty", "in_progress", {
        roomId: "r2",
        inProgressRoomIds: new Set(["r1"]),
      }),
    ).toBe(false);
  });

  it("offers one-tap Clean & Inspected for dirty rooms", () => {
    expect(housekeepingPrimaryAction("dirty")).toEqual({
      status: "clean",
      label: "Mark Clean & Inspected",
    });
    expect(housekeepingPrimaryAction("occupied")).toBeNull();
  });
});

describe("room assignment rules", () => {
  const vacant = { id: "r1", status: "vacant" as const };
  const dirty = { id: "r2", status: "dirty" as const };
  const occupied = { id: "r3", status: "occupied" as const };

  it("allows only vacant rooms for same-day / immediate stays", () => {
    expect(
      isRoomAssignable({
        room: vacant,
        checkIn: "2026-09-28",
        checkOut: "2026-09-30",
        reservations: [],
        today: "2026-09-28",
        immediate: true,
      }),
    ).toBe(true);
    expect(
      isRoomAssignable({
        room: dirty,
        checkIn: "2026-09-28",
        checkOut: "2026-09-30",
        reservations: [],
        today: "2026-09-28",
        immediate: true,
      }),
    ).toBe(false);
  });

  it("allows dirty rooms only from available_from (or day after tomorrow)", () => {
    expect(
      isRoomAssignable({
        room: dirty,
        checkIn: "2026-09-29",
        checkOut: "2026-10-01",
        reservations: [],
        today: "2026-09-28",
      }),
    ).toBe(false);
    expect(
      isRoomAssignable({
        room: { ...dirty, available_from: "2026-10-02" },
        checkIn: "2026-10-01",
        checkOut: "2026-10-03",
        reservations: [],
        today: "2026-09-28",
      }),
    ).toBe(false);
    expect(
      isRoomAssignable({
        room: { ...dirty, available_from: "2026-10-02" },
        checkIn: "2026-10-02",
        checkOut: "2026-10-04",
        reservations: [],
        today: "2026-09-28",
      }),
    ).toBe(true);
  });

  it("blocks maintenance rooms entirely until marked clean", () => {
    expect(
      isRoomAssignable({
        room: { id: "r4", status: "out_of_order" as const },
        checkIn: "2026-10-05",
        checkOut: "2026-10-06",
        reservations: [],
        today: "2026-09-28",
      }),
    ).toBe(false);
    expect(
      isRoomAssignable({
        room: { id: "r4", status: "out_of_order" as const, available_from: "2026-10-05" },
        checkIn: "2026-10-05",
        checkOut: "2026-10-06",
        reservations: [],
        today: "2026-09-28",
      }),
    ).toBe(false);
  });

  it("blocks rooms already booked on overlapping dates", () => {
    const reservations = [
      {
        id: "res-1",
        hotel_id: "h",
        guest_id: "g",
        room_id: "r1",
        room_type_id: "t",
        check_in: "2026-09-30",
        check_out: "2026-10-02",
        status: "reserved" as const,
        rate: "100",
      },
    ];
    expect(
      isRoomAssignable({
        room: vacant,
        checkIn: "2026-09-30",
        checkOut: "2026-10-01",
        reservations,
        today: "2026-09-28",
      }),
    ).toBe(false);
    expect(
      isRoomAssignable({
        room: vacant,
        checkIn: "2026-10-02",
        checkOut: "2026-10-04",
        reservations,
        today: "2026-09-28",
      }),
    ).toBe(true);
    expect(
      isRoomAssignable({
        room: occupied,
        checkIn: "2026-10-05",
        checkOut: "2026-10-06",
        reservations: [],
        today: "2026-09-28",
      }),
    ).toBe(true);
    expect(
      isRoomAssignable({
        room: occupied,
        checkIn: "2026-09-28",
        checkOut: "2026-09-29",
        reservations: [],
        today: "2026-09-28",
        immediate: true,
      }),
    ).toBe(false);
  });
});