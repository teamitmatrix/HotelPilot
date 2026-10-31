/** Client-side Excel (SpreadsheetML) + PDF export for hotel report pages. */

export type ReportCell = string | number | null | undefined;

export type ReportSheet = {
  name: string;
  headers?: string[];
  rows: ReportCell[][];
};

export type ReportExportDoc = {
  title: string;
  subtitle?: string;
  kpis?: Array<{ label: string; value: ReportCell }>;
  sheets: ReportSheet[];
};

function cellText(value: ReportCell): string {
  if (value == null) return "";
  return String(value);
}

function escapeXml(value: ReportCell): string {
  return cellText(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function escapePdf(value: ReportCell): string {
  return cellText(value)
    .replace(/\\/g, "\\\\")
    .replace(/\(/g, "\\(")
    .replace(/\)/g, "\\)");
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function sanitizeFilename(name: string): string {
  return name.replace(/[^\w.\-]+/g, "-").replace(/-+/g, "-").replace(/^-|-$/g, "") || "report";
}

function sheetName(name: string, used: Set<string>): string {
  const base = name.replace(/[\\/*?:[\]]/g, " ").trim().slice(0, 31) || "Sheet";
  let candidate = base;
  let n = 2;
  while (used.has(candidate.toLowerCase())) {
    const suffix = ` (${n})`;
    candidate = `${base.slice(0, Math.max(1, 31 - suffix.length))}${suffix}`;
    n += 1;
  }
  used.add(candidate.toLowerCase());
  return candidate;
}

function isNumericCell(cell: ReportCell): boolean {
  if (typeof cell === "number" && Number.isFinite(cell)) return true;
  const text = cellText(cell).trim();
  if (!text) return false;
  return /^-?\d+(\.\d+)?$/.test(text);
}

function buildSpreadsheetMl(doc: ReportExportDoc): string {
  const used = new Set<string>();
  const worksheets: string[] = [];

  const allSheets: ReportSheet[] = [];
  if (doc.kpis?.length) {
    allSheets.push({
      name: "Summary",
      headers: ["Metric", "Value"],
      rows: doc.kpis.map((k) => [k.label, k.value]),
    });
  }
  for (const sheet of doc.sheets) {
    allSheets.push(sheet);
  }
  if (allSheets.length === 0) {
    allSheets.push({ name: "Report", rows: [["No data"]] });
  }

  for (const sheet of allSheets) {
    const name = sheetName(sheet.name, used);
    const lines: string[] = [];
    if (sheet.headers?.length) {
      lines.push(
        `<Row>${sheet.headers
          .map((h) => `<Cell><Data ss:Type="String">${escapeXml(h)}</Data></Cell>`)
          .join("")}</Row>`,
      );
    }
    for (const row of sheet.rows) {
      lines.push(
        `<Row>${row
          .map((cell) => {
            const type = isNumericCell(cell) ? "Number" : "String";
            return `<Cell><Data ss:Type="${type}">${escapeXml(cell)}</Data></Cell>`;
          })
          .join("")}</Row>`,
      );
    }
    worksheets.push(
      `<Worksheet ss:Name="${escapeXml(name)}"><Table>${lines.join("")}</Table></Worksheet>`,
    );
  }

  return (
    `<?xml version="1.0"?>\n` +
    `<?mso-application progid="Excel.Sheet"?>\n` +
    `<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"\n` +
    ` xmlns:o="urn:schemas-microsoft-com:office:office"\n` +
    ` xmlns:x="urn:schemas-microsoft-com:office:excel"\n` +
    ` xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"\n` +
    ` xmlns:html="http://www.w3.org/TR/REC-html40">\n` +
    `<DocumentProperties xmlns="urn:schemas-microsoft-com:office:office">` +
    `<Title>${escapeXml(doc.title)}</Title></DocumentProperties>\n` +
    worksheets.join("\n") +
    `\n</Workbook>`
  );
}

function latin1Bytes(content: string): Uint8Array {
  const bytes = new Uint8Array(content.length);
  for (let i = 0; i < content.length; i++) {
    const code = content.charCodeAt(i);
    bytes[i] = code > 255 ? 63 : code;
  }
  return bytes;
}

function buildPdf(doc: ReportExportDoc): Uint8Array {
  const lines: string[] = [
    doc.title,
    ...(doc.subtitle ? [doc.subtitle] : []),
    `Generated: ${new Date().toISOString().slice(0, 19).replace("T", " ")} UTC`,
    "",
  ];

  if (doc.kpis?.length) {
    lines.push("Summary", "-".repeat(56));
    for (const kpi of doc.kpis) {
      lines.push(`${kpi.label}: ${cellText(kpi.value)}`);
    }
    lines.push("");
  }

  for (const sheet of doc.sheets) {
    lines.push(sheet.name, "-".repeat(56));
    if (sheet.headers?.length) {
      lines.push(sheet.headers.join(" | "));
    }
    for (const row of sheet.rows) {
      lines.push(row.map(cellText).join(" | "));
    }
    lines.push("");
  }

  lines.push("HotelPilot Reports");

  const pages: string[][] = [];
  const maxLines = 58;
  for (let i = 0; i < lines.length; i += maxLines) {
    pages.push(lines.slice(i, i + maxLines));
  }
  if (pages.length === 0) pages.push([doc.title]);

  const enc = (s: string) => new TextEncoder().encode(s);
  const pageObjIds: number[] = [];
  const pageContentIds: number[] = [];
  let nextId = 4;
  for (let p = 0; p < pages.length; p++) {
    pageObjIds.push(nextId++);
    pageContentIds.push(nextId++);
  }

  const fontId = 3;
  const pagesId = 2;
  const catalogId = 1;

  function pageContent(pageLines: string[]): Uint8Array {
    const y = 780;
    const ops = ["BT", "/F1 9 Tf", "11 TL"];
    let first = true;
    for (const line of pageLines) {
      const safe = escapePdf(line.slice(0, 110));
      if (first) {
        ops.push(`1 0 0 1 36 ${y} Tm (${safe}) Tj`);
        first = false;
      } else {
        ops.push("T*");
        ops.push(`(${safe}) Tj`);
      }
    }
    ops.push("ET");
    return latin1Bytes(ops.join("\n"));
  }

  const kids = pageObjIds.map((id) => `${id} 0 R`).join(" ");
  const objMap = new Map<number, Uint8Array>();

  objMap.set(
    catalogId,
    enc(`1 0 obj<< /Type /Catalog /Pages ${pagesId} 0 R >>endobj\n`),
  );
  objMap.set(
    pagesId,
    enc(
      `2 0 obj<< /Type /Pages /Kids [${kids}] /Count ${pages.length} >>endobj\n`,
    ),
  );
  objMap.set(
    fontId,
    enc(`3 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n`),
  );

  pages.forEach((pageLines, idx) => {
    const pageId = pageObjIds[idx];
    const contentId = pageContentIds[idx];
    const content = pageContent(pageLines);
    objMap.set(
      pageId,
      enc(
        `${pageId} 0 obj<< /Type /Page /Parent ${pagesId} 0 R /MediaBox [0 0 612 792] ` +
          `/Contents ${contentId} 0 R /Resources << /Font << /F1 ${fontId} 0 R >> >> >>endobj\n`,
      ),
    );
    const header = enc(`${contentId} 0 obj<< /Length ${content.length} >>stream\n`);
    const footer = enc(`\nendstream\nendobj\n`);
    const combined = new Uint8Array(header.length + content.length + footer.length);
    combined.set(header, 0);
    combined.set(content, header.length);
    combined.set(footer, header.length + content.length);
    objMap.set(contentId, combined);
  });

  const maxObj = Math.max(...objMap.keys());
  const outParts: Uint8Array[] = [enc("%PDF-1.4\n")];
  const offsets: number[] = [0];
  let size = outParts[0].length;
  for (let id = 1; id <= maxObj; id++) {
    const obj = objMap.get(id);
    if (!obj) continue;
    offsets[id] = size;
    outParts.push(obj);
    size += obj.length;
  }

  const xrefPos = size;
  let xref = `xref\n0 ${maxObj + 1}\n0000000000 65535 f \n`;
  for (let id = 1; id <= maxObj; id++) {
    const off = offsets[id] ?? 0;
    xref += `${String(off).padStart(10, "0")} 00000 n \n`;
  }
  xref += `trailer<< /Size ${maxObj + 1} /Root ${catalogId} 0 R >>\nstartxref\n${xrefPos}\n%%EOF\n`;
  outParts.push(enc(xref));

  const total = outParts.reduce((n, p) => n + p.length, 0);
  const out = new Uint8Array(total);
  let offset = 0;
  for (const part of outParts) {
    out.set(part, offset);
    offset += part.length;
  }
  return out;
}

export function downloadReportExcel(doc: ReportExportDoc, filenameBase: string) {
  const xml = buildSpreadsheetMl(doc);
  const blob = new Blob([xml], { type: "application/vnd.ms-excel" });
  downloadBlob(blob, `${sanitizeFilename(filenameBase)}.xls`);
}

export function downloadReportPdf(doc: ReportExportDoc, filenameBase: string) {
  const pdf = buildPdf(doc);
  // Copy into a fresh ArrayBuffer-backed view for BlobPart typing
  const copy = new Uint8Array(pdf);
  const blob = new Blob([copy], { type: "application/pdf" });
  downloadBlob(blob, `${sanitizeFilename(filenameBase)}.pdf`);
}
