"""Build Night Audit Report PDF (stdlib-only)."""
from __future__ import annotations

from typing import Any


def _esc(text: str) -> str:
    return (
        str(text or "")
        .replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )


def build_night_audit_pdf(summary: dict[str, Any]) -> bytes:
    """Single-page PDF of the night audit KPI summary."""
    hotel = summary.get("hotel") or {}
    kpis = summary.get("kpis") or {}
    currency = str(hotel.get("currency") or summary.get("currency") or "USD")
    lines: list[str] = [
        f"{hotel.get('name') or 'Hotel'} — Night Audit Report",
        f"Business date: {summary.get('business_date')}",
        f"Run id: {summary.get('run_id') or '—'}",
        f"Triggered by: {summary.get('triggered_by') or '—'}",
        "",
        "Key performance indicators",
        "-" * 56,
        f"Room revenue:            {kpis.get('room_revenue')} {currency}",
        f"Tax liabilities:         {kpis.get('tax_liabilities')} {currency}",
        f"Outstanding folio bal.:  {kpis.get('outstanding_folio_balances')} {currency}",
        f"Rooms occupied:          {kpis.get('rooms_occupied')} / {kpis.get('rooms_available')}",
        f"Occupancy rate:          {kpis.get('occupancy_rate_pct')}%",
        f"RevPAR:                  {kpis.get('revpar')} {currency}",
        "",
        "Processing summary",
        "-" * 56,
        f"Room charges posted:     {summary.get('room_charges_posted', 0)}",
        f"Room charges skipped:    {summary.get('room_charges_skipped', 0)}",
        f"Tax lines posted:        {summary.get('tax_lines_posted', 0)}",
        f"No-shows marked:         {summary.get('no_shows_marked', 0)}",
        f"No-show penalties:       {summary.get('no_show_penalties_posted', 0)}",
        f"Cash drawer:             {summary.get('cash_drawer_status') or '—'}",
        f"ERP period check:        {summary.get('erp_period_status') or '—'}",
        "",
        "Generated automatically by HotelPilot Night Audit.",
    ]

    y = 780
    content_ops = ["BT", "/F1 11 Tf", "14 TL"]
    first = True
    for line in lines:
        safe = _esc(line)
        if first:
            content_ops.append(f"1 0 0 1 40 {y} Tm ({safe}) Tj")
            first = False
        else:
            content_ops.append("T*")
            content_ops.append(f"({safe}) Tj")
    content_ops.append("ET")
    content = "\n".join(content_ops).encode("latin-1", errors="replace")

    objects: list[bytes] = [
        b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n",
        b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n",
        (
            b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>endobj\n"
        ),
        (
            f"4 0 obj<< /Length {len(content)} >>stream\n".encode("ascii")
            + content
            + b"\nendstream\nendobj\n"
        ),
        b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    )
    return bytes(out)
