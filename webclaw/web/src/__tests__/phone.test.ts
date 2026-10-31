import { describe, expect, it } from "vitest";
import { formatE164, parsePhoneValue } from "@/lib/phone";

describe("phone E.164 helpers", () => {
  it("formats PK national number with dial code", () => {
    expect(formatE164("92", "3421234567")).toBe("+923421234567");
    expect(formatE164("92", "03421234567")).toBe("+923421234567");
  });

  it("parses stored E.164 back into country + national", () => {
    const parsed = parsePhoneValue("+923421234567");
    expect(parsed.country.iso).toBe("PK");
    expect(parsed.national).toBe("3421234567");
  });
});
