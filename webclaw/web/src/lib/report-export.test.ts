import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { downloadReportExcel, downloadReportPdf } from "./report-export";

describe("report-export", () => {
  const clicks: string[] = [];
  let createObjectURL: typeof URL.createObjectURL;
  let revokeObjectURL: typeof URL.revokeObjectURL;

  beforeEach(() => {
    clicks.length = 0;
    createObjectURL = URL.createObjectURL;
    revokeObjectURL = URL.revokeObjectURL;
    URL.createObjectURL = vi.fn(() => "blob:mock");
    URL.revokeObjectURL = vi.fn();
    vi.spyOn(document, "createElement").mockImplementation(((tag: string) => {
      if (tag === "a") {
        return {
          href: "",
          download: "",
          click: () => clicks.push("click"),
          remove: () => undefined,
        } as unknown as HTMLAnchorElement;
      }
      return document.createElement(tag);
    }) as typeof document.createElement);
    vi.spyOn(document.body, "appendChild").mockImplementation((node) => node);
  });

  afterEach(() => {
    URL.createObjectURL = createObjectURL;
    URL.revokeObjectURL = revokeObjectURL;
    vi.restoreAllMocks();
  });

  it("downloads excel and pdf for a multi-sheet report", () => {
    const doc = {
      title: "Test report",
      subtitle: "2026-01-01 to 2026-01-31",
      kpis: [{ label: "Total", value: 3 }],
      sheets: [
        {
          name: "Rows",
          headers: ["Name", "Amount"],
          rows: [
            ["Alpha", 10],
            ["Beta", 20],
          ],
        },
      ],
    };

    downloadReportExcel(doc, "test-report");
    downloadReportPdf(doc, "test-report");
    expect(clicks).toEqual(["click", "click"]);
    expect(URL.createObjectURL).toHaveBeenCalledTimes(2);
  });
});
