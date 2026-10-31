"""Public guest self-service portal — token-authenticated, no staff JWT."""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import db
from auth.deps import get_current_user, require_hotel_tenant
from rbac import require_permission
from skills.executor import execute_skill

log = logging.getLogger("webclaw.guest_portal")

router = APIRouter(prefix="/api/v1/guest", tags=["guest-portal"])

WEB_ORIGIN = os.environ.get("WEBCLAW_CORS_ORIGIN", "http://localhost:3000")
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _docs_root() -> Path:
    base = os.environ.get("WEBCLAW_GUEST_DOCS_DIR")
    if base:
        root = Path(os.path.expanduser(base))
        if not root.is_absolute():
            try:
                from env_loader import pms_root
                root = Path(pms_root()) / root
            except Exception:
                root = Path.cwd() / root
    else:
        try:
            from env_loader import resolve_webclaw_db_path
            root = Path(resolve_webclaw_db_path()).parent / "guest-docs"
        except Exception:
            root = Path("data") / "guest-docs"
            try:
                from env_loader import pms_root
                root = Path(pms_root()) / root
            except Exception:
                root = root.resolve()
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _erp():
    return db.get_skill_db("erpclaw") or db.get_skill_db("hospitalityclaw")


def _row(row, key, idx=0):
    if row is None:
        return None
    if hasattr(row, "keys"):
        try:
            return row[key]
        except Exception:
            return None
    try:
        return row[idx]
    except Exception:
        return None


def _ensure_portal_schema(conn) -> None:
    try:
        import sys
        from env_loader import pms_root
        hosp_parent = pms_root()
        if hosp_parent not in sys.path:
            sys.path.insert(0, hosp_parent)
        from hospitalityclaw.init_db import init_db
        init_db(conn)
    except Exception:
        log.exception("guest portal schema ensure failed")


def lookup_by_token(token: str):
    """Return (conn, reservation_row_dict) or (None, None). Caller must close conn."""
    if not token or not _TOKEN_RE.match(token):
        return None, None
    conn = _erp()
    if not conn:
        return None, None
    try:
        _ensure_portal_schema(conn)
        row = conn.execute(
            """SELECT r.*,
                      g.first_name, g.last_name, g.email AS guest_email, g.phone AS guest_phone,
                      g.nationality, g.id_number,
                      rt.name AS room_type_name, rm.room_number,
                      h.name AS hotel_name, h.currency, h.address AS hotel_address,
                      h.phone AS hotel_phone, h.email AS hotel_email, h.settings_json,
                      f.id AS folio_id, f.balance AS folio_balance, f.status AS folio_status
               FROM hospitalityclaw_reservation r
               JOIN hospitalityclaw_guest g ON g.id = r.guest_id
               JOIN hospitalityclaw_room_type rt ON rt.id = r.room_type_id
               JOIN hospitalityclaw_hotel h ON h.id = r.hotel_id
               LEFT JOIN hospitalityclaw_room rm ON rm.id = r.room_id
               LEFT JOIN hospitalityclaw_folio f ON f.reservation_id = r.id AND f.tenant_id = r.tenant_id
               WHERE r.booking_token = ?""",
            (token,),
        ).fetchone()
        if not row:
            conn.close()
            return None, None
        data = {k: row[k] for k in row.keys()} if hasattr(row, "keys") else dict(row)
        return conn, data
    except Exception:
        log.exception("lookup_by_token failed")
        try:
            conn.close()
        except Exception:
            pass
        return None, None


def _portal_blocked(status: str) -> str | None:
    if status in ("cancelled", "no_show"):
        return "This reservation is no longer available."
    if status == "checked_out":
        return "This stay has already been checked out."
    return None


def _list_documents(conn, tenant_id: str, reservation_id: str) -> list[dict]:
    try:
        rows = conn.execute(
            """SELECT id, doc_type, file_name, mime_type, created_at
               FROM hospitalityclaw_guest_document
               WHERE tenant_id = ? AND reservation_id = ?
               ORDER BY created_at DESC""",
            (tenant_id, reservation_id),
        ).fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        out.append(
            {
                "id": _row(r, "id", 0),
                "doc_type": _row(r, "doc_type", 1),
                "file_name": _row(r, "file_name", 2),
                "mime_type": _row(r, "mime_type", 3),
                "created_at": _row(r, "created_at", 4),
            }
        )
    return out


def _list_charges(conn, tenant_id: str, folio_id: str | None) -> list[dict]:
    if not folio_id:
        return []
    rows = conn.execute(
        """SELECT id, description, amount, charge_type, posting_date
           FROM hospitalityclaw_folio_charge
           WHERE tenant_id = ? AND folio_id = ?
           ORDER BY posting_date, created_at""",
        (tenant_id, folio_id),
    ).fetchall()
    return [
        {
            "id": _row(r, "id", 0),
            "description": _row(r, "description", 1),
            "amount": str(_row(r, "amount", 2) or "0"),
            "charge_type": _row(r, "charge_type", 3),
            "posting_date": _row(r, "posting_date", 4),
        }
        for r in rows
    ]


def _settings(raw) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _serialize_stay(data: dict, docs: list[dict], charges: list[dict]) -> dict:
    settings = _settings(data.get("settings_json"))
    status = str(data.get("status") or "")
    conf = str(data.get("id") or "").replace("-", "")[:8].upper()
    return {
        "confirmation_code": conf,
        "status": status,
        "pre_checked_in": status == "pre_checked_in",
        "checked_in": status == "checked_in",
        "checkout_requested": bool(data.get("checkout_requested_at")),
        "registration_signed": bool(data.get("registration_signed_at")),
        "hotel": {
            "name": data.get("hotel_name"),
            "address": data.get("hotel_address"),
            "phone": data.get("hotel_phone"),
            "email": data.get("hotel_email"),
            "currency": data.get("currency") or "USD",
            "check_in_time": settings.get("check_in_time") or "15:00",
            "check_out_time": settings.get("check_out_time") or "11:00",
        },
        "guest": {
            "first_name": data.get("first_name"),
            "last_name": data.get("last_name"),
            "email": data.get("guest_email"),
            "phone": data.get("guest_phone"),
            "nationality": data.get("nationality"),
            "id_number": data.get("id_number"),
        },
        "stay": {
            "check_in": str(data.get("check_in") or "")[:10],
            "check_out": str(data.get("check_out") or "")[:10],
            "room_type": data.get("room_type_name"),
            "room_number": data.get("room_number"),
            "rate": str(data.get("rate") or "0"),
            "adults": data.get("adults"),
            "children": data.get("children"),
        },
        "folio": {
            "id": data.get("folio_id"),
            "balance": str(data.get("folio_balance") or "0"),
            "status": data.get("folio_status"),
            "charges": charges,
        },
        "documents": docs,
        "can_pre_register": status in ("reserved", "pre_checked_in"),
        "can_pay": status in ("reserved", "pre_checked_in", "checked_in")
        and Decimal(str(data.get("folio_balance") or "0")) > 0,
        "can_request_checkout": status == "checked_in",
    }


async def _emit_reservation_change():
    try:
        from events import emit_data_change
        await emit_data_change("hospitalityclaw", "reservation")
    except Exception:
        pass


# ── Routes ───────────────────────────────────────────────────────────────────

@router.get("/{token}")
async def get_guest_stay(token: str):
    conn, data = lookup_by_token(token)
    if not data:
        return JSONResponse({"status": "error", "message": "Invalid or expired booking link"}, 404)
    try:
        blocked = _portal_blocked(str(data.get("status") or ""))
        if blocked and str(data.get("status")) not in ("checked_out",):
            # Still show checked_out summary; block cancelled
            if str(data.get("status")) in ("cancelled", "no_show"):
                return JSONResponse({"status": "error", "message": blocked}, 410)
        docs = _list_documents(conn, data["tenant_id"], data["id"])
        charges = _list_charges(conn, data["tenant_id"], data.get("folio_id"))
        return {"status": "ok", "stay": _serialize_stay(data, docs, charges)}
    finally:
        conn.close()


class PreRegisterRequest(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    nationality: str | None = None
    id_number: str | None = None
    signature_data_url: str | None = Field(
        default=None,
        description="PNG/JPEG data URL of digital registration signature",
    )


@router.post("/{token}/pre-register")
async def guest_pre_register(token: str, request: Request):
    """Upload registration details + digital signature → status pre_checked_in."""
    try:
        body = await request.json()
        req = PreRegisterRequest(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)

    conn, data = lookup_by_token(token)
    if not data:
        return JSONResponse({"status": "error", "message": "Invalid or expired booking link"}, 404)
    try:
        status = str(data.get("status") or "")
        if status not in ("reserved", "pre_checked_in"):
            return JSONResponse(
                {"status": "error", "message": f"Cannot pre-register from status '{status}'"},
                400,
            )

        guest_id = data["guest_id"]
        tenant_id = data["tenant_id"]
        rid = data["id"]

        # Update guest profile fields when provided
        updates = []
        params = []
        for col, val in (
            ("first_name", req.first_name),
            ("last_name", req.last_name),
            ("email", req.email),
            ("phone", req.phone),
            ("nationality", req.nationality),
            ("id_number", req.id_number),
        ):
            if val is not None and str(val).strip():
                updates.append(f"{col} = ?")
                params.append(str(val).strip())
        if updates:
            params.extend([guest_id, tenant_id])
            conn.execute(
                f"UPDATE hospitalityclaw_guest SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP "
                f"WHERE id = ? AND tenant_id = ?",
                params,
            )

        # Persist signature as document if provided
        if req.signature_data_url and req.signature_data_url.startswith("data:image"):
            try:
                header, b64 = req.signature_data_url.split(",", 1)
                mime = "image/png"
                if "jpeg" in header or "jpg" in header:
                    mime = "image/jpeg"
                raw = base64.b64decode(b64)
                if len(raw) > MAX_UPLOAD_BYTES:
                    return JSONResponse({"status": "error", "message": "Signature image too large"}, 400)
                doc_id = str(uuid.uuid4())
                rel = Path(tenant_id) / rid / f"signature-{doc_id}.png"
                path = _docs_root() / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                conn.execute(
                    """INSERT INTO hospitalityclaw_guest_document
                       (id, tenant_id, guest_id, reservation_id, doc_type, file_name, mime_type, storage_path)
                       VALUES (?, ?, ?, ?, 'registration_signature', ?, ?, ?)""",
                    (doc_id, tenant_id, guest_id, rid, "signature.png", mime, str(rel)),
                )
                conn.execute(
                    """UPDATE hospitalityclaw_reservation
                       SET registration_signed_at = ?, updated_at = CURRENT_TIMESTAMP
                       WHERE id = ? AND tenant_id = ?""",
                    (_now(), rid, tenant_id),
                )
            except Exception as e:
                log.exception("signature save failed")
                return JSONResponse({"status": "error", "message": f"Could not save signature: {e}"}, 400)

        # Mark pre-checked in
        conn.execute(
            """UPDATE hospitalityclaw_reservation
               SET status = 'pre_checked_in', pre_check_in_at = COALESCE(pre_check_in_at, ?),
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ? AND tenant_id = ?""",
            (_now(), rid, tenant_id),
        )
        conn.commit()
        await _emit_reservation_change()

        # Reload
        row = conn.execute(
            """SELECT r.*, g.first_name, g.last_name, g.email AS guest_email, g.phone AS guest_phone,
                      g.nationality, g.id_number,
                      rt.name AS room_type_name, rm.room_number,
                      h.name AS hotel_name, h.currency, h.address AS hotel_address,
                      h.phone AS hotel_phone, h.email AS hotel_email, h.settings_json,
                      f.id AS folio_id, f.balance AS folio_balance, f.status AS folio_status
               FROM hospitalityclaw_reservation r
               JOIN hospitalityclaw_guest g ON g.id = r.guest_id
               JOIN hospitalityclaw_room_type rt ON rt.id = r.room_type_id
               JOIN hospitalityclaw_hotel h ON h.id = r.hotel_id
               LEFT JOIN hospitalityclaw_room rm ON rm.id = r.room_id
               LEFT JOIN hospitalityclaw_folio f ON f.reservation_id = r.id AND f.tenant_id = r.tenant_id
               WHERE r.id = ?""",
            (rid,),
        ).fetchone()
        data2 = {k: row[k] for k in row.keys()}
        docs = _list_documents(conn, tenant_id, rid)
        charges = _list_charges(conn, tenant_id, data2.get("folio_id"))
        return {
            "status": "ok",
            "message": "Pre-registration complete. Front desk has been notified.",
            "stay": _serialize_stay(data2, docs, charges),
        }
    finally:
        conn.close()


@router.post("/{token}/id-document")
async def upload_id_document(
    token: str,
    file: UploadFile = File(...),
    doc_type: str = Form("id_photo"),
):
    if doc_type not in ("id_photo", "passport", "other"):
        doc_type = "id_photo"
    conn, data = lookup_by_token(token)
    if not data:
        return JSONResponse({"status": "error", "message": "Invalid or expired booking link"}, 404)
    try:
        status = str(data.get("status") or "")
        if status not in ("reserved", "pre_checked_in", "checked_in"):
            return JSONResponse({"status": "error", "message": "Cannot upload for this stay"}, 400)

        content = await file.read()
        if not content:
            return JSONResponse({"status": "error", "message": "Empty file"}, 400)
        if len(content) > MAX_UPLOAD_BYTES:
            return JSONResponse({"status": "error", "message": "File exceeds 5 MB limit"}, 400)
        mime = (file.content_type or "application/octet-stream").split(";")[0].strip().lower()
        if mime not in ALLOWED_IMAGE_TYPES:
            return JSONResponse(
                {"status": "error", "message": "Only JPEG, PNG, WebP, or GIF images are accepted"},
                400,
            )

        tenant_id = data["tenant_id"]
        rid = data["id"]
        guest_id = data["guest_id"]
        doc_id = str(uuid.uuid4())
        ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}.get(
            mime, ".bin"
        )
        original = (file.filename or f"id{ext}")[:120]
        rel = Path(tenant_id) / rid / f"{doc_type}-{doc_id}{ext}"
        path = _docs_root() / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

        conn.execute(
            """INSERT INTO hospitalityclaw_guest_document
               (id, tenant_id, guest_id, reservation_id, doc_type, file_name, mime_type, storage_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (doc_id, tenant_id, guest_id, rid, doc_type, original, mime, str(rel)),
        )
        # If still reserved, bump to pre_checked_in when ID is uploaded
        if status == "reserved":
            conn.execute(
                """UPDATE hospitalityclaw_reservation
                   SET status = 'pre_checked_in',
                       pre_check_in_at = COALESCE(pre_check_in_at, ?),
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = ? AND tenant_id = ?""",
                (_now(), rid, tenant_id),
            )
        conn.commit()
        await _emit_reservation_change()
        return {
            "status": "ok",
            "document": {
                "id": doc_id,
                "doc_type": doc_type,
                "file_name": original,
                "mime_type": mime,
            },
            "message": "ID document uploaded. Front desk can review it on the arrivals board.",
        }
    finally:
        conn.close()


@router.get("/{token}/documents/{doc_id}")
async def get_document(token: str, doc_id: str):
    conn, data = lookup_by_token(token)
    if not data:
        return JSONResponse({"status": "error", "message": "Invalid link"}, 404)
    try:
        row = conn.execute(
            """SELECT storage_path, mime_type, file_name FROM hospitalityclaw_guest_document
               WHERE id = ? AND tenant_id = ? AND reservation_id = ?""",
            (doc_id, data["tenant_id"], data["id"]),
        ).fetchone()
        if not row:
            return JSONResponse({"status": "error", "message": "Document not found"}, 404)
        path = _docs_root() / str(_row(row, "storage_path", 0))
        if not path.is_file():
            return JSONResponse({"status": "error", "message": "File missing"}, 404)
        return FileResponse(
            path,
            media_type=str(_row(row, "mime_type", 1) or "application/octet-stream"),
            filename=str(_row(row, "file_name", 2) or "document"),
        )
    finally:
        conn.close()


@router.post("/{token}/payment-intent")
async def guest_payment_intent(token: str, request: Request):
    conn, data = lookup_by_token(token)
    if not data:
        return JSONResponse({"status": "error", "message": "Invalid or expired booking link"}, 404)
    try:
        status = str(data.get("status") or "")
        if status in ("cancelled", "no_show", "checked_out"):
            return JSONResponse({"status": "error", "message": "Stay is not payable"}, 400)
        folio_id = data.get("folio_id")
        if not folio_id:
            return JSONResponse({"status": "error", "message": "No folio for this stay"}, 400)
        balance = Decimal(str(data.get("folio_balance") or "0"))
        if balance <= 0:
            return JSONResponse({"status": "error", "message": "Nothing due on this folio"}, 400)

        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        amount = str(body.get("amount") or balance)
        currency = str(data.get("currency") or "USD")
        guest_name = f"{data.get('first_name') or ''} {data.get('last_name') or ''}".strip()

        import stripe_billing as sb

        result = sb.create_folio_payment_intent(
            tenant_id=data["tenant_id"],
            folio_id=folio_id,
            amount=amount,
            currency=currency,
            guest_name=guest_name or None,
            description=f"Guest portal folio {folio_id}",
        )
        return {"status": "ok", **result}
    except ValueError as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    except Exception as e:
        log.exception("guest payment-intent failed")
        return JSONResponse({"status": "error", "message": f"Payment error: {e}"}, 502)
    finally:
        conn.close()


class GuestConfirmPayment(BaseModel):
    payment_intent_id: str = Field(min_length=3, max_length=120)


@router.post("/{token}/payment-confirm")
async def guest_payment_confirm(token: str, request: Request):
    try:
        body = await request.json()
        req = GuestConfirmPayment(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)

    conn, data = lookup_by_token(token)
    if not data:
        return JSONResponse({"status": "error", "message": "Invalid or expired booking link"}, 404)
    try:
        folio_id = data.get("folio_id")
        tenant_id = data["tenant_id"]
        if not folio_id:
            return JSONResponse({"status": "error", "message": "No folio"}, 400)

        import stripe_billing as sb

        intent = sb.retrieve_folio_payment_intent(tenant_id, req.payment_intent_id)
        intent_status = intent.get("status") if isinstance(intent, dict) else getattr(intent, "status", None)
        meta = intent.get("metadata") if isinstance(intent, dict) else getattr(intent, "metadata", {}) or {}
        if hasattr(meta, "to_dict"):
            meta = meta.to_dict()
        if str(meta.get("folio_id") or "") != str(folio_id):
            return JSONResponse({"status": "error", "message": "PaymentIntent folio mismatch"}, 400)
        if str(meta.get("tenant_id") or "") != str(tenant_id):
            return JSONResponse({"status": "error", "message": "PaymentIntent tenant mismatch"}, 403)
        if intent_status != "succeeded":
            return JSONResponse(
                {"status": "error", "message": f"Payment not succeeded (status={intent_status})"},
                400,
            )

        # Idempotency
        existing = conn.execute(
            """SELECT id FROM hospitalityclaw_transaction
               WHERE tenant_id = ? AND folio_id = ? AND reference = ? LIMIT 1""",
            (tenant_id, folio_id, req.payment_intent_id),
        ).fetchone()
        if existing:
            return {
                "status": "ok",
                "settled": True,
                "already_posted": True,
                "payment_intent_id": req.payment_intent_id,
            }

        settle = await execute_skill(
            "hospitalityclaw",
            "settle-folio",
            {
                "folio_id": folio_id,
                "payment_method": "card",
                "reference": req.payment_intent_id,
                "description": f"Guest portal card payment ({req.payment_intent_id})",
            },
            tenant_id=tenant_id,
        )
        if settle.get("status") != "ok":
            return JSONResponse(
                {
                    "status": "error",
                    "message": settle.get("message") or "Settle failed",
                    "payment_captured": True,
                },
                502,
            )
        await _emit_reservation_change()
        return {
            "status": "ok",
            "settled": True,
            "amount": settle.get("amount"),
            "gl_posted": settle.get("gl_posted"),
            "payment_intent_id": req.payment_intent_id,
            "message": "Payment received. Thank you!",
        }
    except ValueError as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    except Exception as e:
        log.exception("guest payment-confirm failed")
        return JSONResponse({"status": "error", "message": str(e)}, 502)
    finally:
        conn.close()


@router.post("/{token}/request-checkout")
async def guest_request_checkout(token: str):
    conn, data = lookup_by_token(token)
    if not data:
        return JSONResponse({"status": "error", "message": "Invalid or expired booking link"}, 404)
    try:
        status = str(data.get("status") or "")
        if status != "checked_in":
            return JSONResponse(
                {"status": "error", "message": "Checkout can only be requested while in-house"},
                400,
            )
        balance = Decimal(str(data.get("folio_balance") or "0"))
        if balance > 0:
            return JSONResponse(
                {
                    "status": "error",
                    "message": f"Please settle the remaining balance of {balance} before requesting checkout",
                    "balance": str(balance),
                },
                400,
            )
        conn.execute(
            """UPDATE hospitalityclaw_reservation
               SET checkout_requested_at = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ? AND tenant_id = ?""",
            (_now(), data["id"], data["tenant_id"]),
        )
        # Append note for front desk
        note = "[Guest portal] Checkout requested"
        existing_notes = data.get("notes") or ""
        if note not in existing_notes:
            notes = f"{existing_notes}\n{note}".strip() if existing_notes else note
            conn.execute(
                "UPDATE hospitalityclaw_reservation SET notes = ? WHERE id = ? AND tenant_id = ?",
                (notes, data["id"], data["tenant_id"]),
            )
        conn.commit()
        await _emit_reservation_change()
        return {
            "status": "ok",
            "message": "Checkout requested. Front desk will complete your departure shortly.",
            "checkout_requested": True,
        }
    finally:
        conn.close()


# ── Staff: review guest portal documents on the desk ─────────────────────────

staff_router = APIRouter(prefix="/api/v1/hotel", tags=["guest-portal-staff"])


@staff_router.get("/reservations/{reservation_id}/guest-documents")
@require_permission("bookings.read")
async def staff_list_guest_documents(
    reservation_id: str,
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
):
    conn = _erp()
    if not conn:
        return JSONResponse({"status": "error", "message": "Database unavailable"}, 503)
    try:
        _ensure_portal_schema(conn)
        res = conn.execute(
            "SELECT id, booking_token, status, pre_check_in_at, checkout_requested_at, registration_signed_at "
            "FROM hospitalityclaw_reservation WHERE id = ? AND tenant_id = ?",
            (reservation_id, tenant_id),
        ).fetchone()
        if not res:
            return JSONResponse({"status": "error", "message": "Reservation not found"}, 404)
        docs = _list_documents(conn, tenant_id, reservation_id)
        origin = WEB_ORIGIN.rstrip("/")
        token = _row(res, "booking_token", 1)
        return {
            "status": "ok",
            "reservation_id": reservation_id,
            "reservation_status": _row(res, "status", 2),
            "pre_check_in_at": _row(res, "pre_check_in_at", 3),
            "checkout_requested_at": _row(res, "checkout_requested_at", 4),
            "registration_signed_at": _row(res, "registration_signed_at", 5),
            "portal_url": f"{origin}/guest/{token}" if token else None,
            "documents": docs,
        }
    finally:
        conn.close()


@staff_router.get("/guest-documents/{doc_id}")
@require_permission("bookings.read")
async def staff_get_guest_document(
    doc_id: str,
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
):
    conn = _erp()
    if not conn:
        return JSONResponse({"status": "error", "message": "Database unavailable"}, 503)
    try:
        row = conn.execute(
            """SELECT storage_path, mime_type, file_name FROM hospitalityclaw_guest_document
               WHERE id = ? AND tenant_id = ?""",
            (doc_id, tenant_id),
        ).fetchone()
        if not row:
            return JSONResponse({"status": "error", "message": "Document not found"}, 404)
        path = _docs_root() / str(_row(row, "storage_path", 0))
        if not path.is_file():
            return JSONResponse({"status": "error", "message": "File missing"}, 404)
        return FileResponse(
            path,
            media_type=str(_row(row, "mime_type", 1) or "application/octet-stream"),
            filename=str(_row(row, "file_name", 2) or "document"),
        )
    finally:
        conn.close()