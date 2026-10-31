"""Generate a simple itemized folio PDF receipt (stdlib-only PDF)."""
from __future__ import annotations

from decimal import Decimal
from typing import Any


def _esc(text: str) -> str:
    return (
        str(text or "")
        .replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )


def build_folio_pdf(
    *,
    hotel_name: str,
    guest_name: str,
    folio_id: str,
    confirmation_code: str,
    checkin_date: str,
    checkout_date: str,
    currency: str,
    charges: list[dict[str, Any]],
    total_amount: str,
    balance: str,
) -> bytes:
    """Build a minimal single-page PDF without third-party libraries."""
    lines: list[str] = []
    lines.append(f"{hotel_name} — Folio Receipt")
    lines.append("")
    lines.append(f"Guest: {guest_name}")
    lines.append(f"Confirmation: {confirmation_code}")
    lines.append(f"Folio: {folio_id}")
    lines.append(f"Stay: {checkin_date} → {checkout_date}")
    lines.append("")
    lines.append("Itemized charges")
    lines.append("-" * 56)
    for c in charges:
        desc = str(c.get("description") or c.get("charge_type") or "Charge")[:36]
        amt = c.get("amount") or "0"
        ctype = str(c.get("charge_type") or "")
        pdate = str(c.get("posting_date") or "")[:10]
        try:
            signed = Decimal(str(amt))
            if ctype == "payment":
                signed = -abs(signed)
        except Exception:
            signed = amt
        lines.append(f"{pdate}  {desc:<36} {signed:>10} {currency}")
    lines.append("-" * 56)
    lines.append(f"Total charged: {total_amount} {currency}")
    lines.append(f"Balance due:   {balance} {currency}")
    lines.append("")
    lines.append("Thank you for your stay.")

    # PDF content stream — Helvetica, top-down text
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

    objects: list[bytes] = []
    objects.append(b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n")
    objects.append(b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n")
    objects.append(
        b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>endobj\n"
    )
    objects.append(
        f"4 0 obj<< /Length {len(content)} >>stream\n".encode("ascii")
        + content
        + b"\nendstream\nendobj\n"
    )
    objects.append(
        b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n"
    )

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
