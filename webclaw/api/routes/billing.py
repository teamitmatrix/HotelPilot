"""Stripe billing routes — SaaS Checkout, webhooks, and guest folio PaymentIntents."""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import db
from auth.deps import get_current_user, get_optional_tenant, require_hotel_tenant
from rbac import require_permission
from skills.executor import execute_skill
import stripe_billing as sb

log = logging.getLogger("webclaw.billing")

router = APIRouter(tags=["billing"])

WEB_ORIGIN = os.environ.get("WEBCLAW_CORS_ORIGIN", "http://localhost:3000")


class CheckoutRequest(BaseModel):
    plan_code: str = Field(default="starter", min_length=2, max_length=40)
    billing_cycle: str = Field(default="monthly")
    success_url: str | None = None
    cancel_url: str | None = None
    trial_days: int | None = Field(default=None, ge=0, le=90)
    tenant_id: str | None = None  # SuperAdmin may bill on behalf of a hotel


class PortalRequest(BaseModel):
    return_url: str | None = None
    tenant_id: str | None = None


class FolioPaymentIntentRequest(BaseModel):
    folio_id: str = Field(min_length=1, max_length=80)
    amount: str | None = None  # defaults to folio balance
    currency: str | None = None
    description: str | None = None


class FolioConfirmRequest(BaseModel):
    folio_id: str = Field(min_length=1, max_length=80)
    payment_intent_id: str = Field(min_length=3, max_length=120)


class FolioDepositIntentRequest(BaseModel):
    folio_id: str = Field(min_length=1, max_length=80)
    amount: str | None = None  # defaults to hotel deposit policy
    currency: str | None = None
    description: str | None = None


class FolioDepositConfirmRequest(BaseModel):
    folio_id: str = Field(min_length=1, max_length=80)
    payment_intent_id: str = Field(min_length=3, max_length=120)


def _normalize_plan(code: str) -> str:
    value = (code or "starter").strip().lower()
    if value == "professional":
        return "business"
    if value not in ("starter", "business", "enterprise"):
        return "starter"
    return value


def _tenant_meta(tenant_id: str) -> dict:
    erp = db.get_skill_db("erpclaw")
    if not erp:
        return {}
    try:
        row = erp.execute(
            """SELECT id, hotel_name, admin_email, currency, subscription_tier, status
               FROM pms_tenant WHERE id = ? OR company_id = ?""",
            (tenant_id, tenant_id),
        ).fetchone()
        if not row:
            return {}
        return {
            "tenant_id": row[0] if isinstance(row, tuple) else row["id"],
            "hotel_name": row[1] if isinstance(row, tuple) else row["hotel_name"],
            "admin_email": row[2] if isinstance(row, tuple) else row["admin_email"],
            "currency": row[3] if isinstance(row, tuple) else row["currency"],
            "subscription_tier": row[4] if isinstance(row, tuple) else row["subscription_tier"],
            "status": row[5] if isinstance(row, tuple) else row["status"],
        }
    except Exception:
        return {}
    finally:
        erp.close()


def _resolve_billing_tenant(request: Request, explicit: str | None) -> tuple[str | None, str | None]:
    """Return (tenant_id, error_message)."""
    ctx = get_optional_tenant(request)
    if explicit:
        if ctx and (ctx.is_superadmin or ctx.isolation_id == explicit):
            return explicit, None
        if ctx and explicit in (ctx.assigned_properties or []):
            return explicit, None
        return None, "Not allowed to bill for this tenant"
    if ctx and ctx.isolation_id:
        return ctx.isolation_id, None
    return None, "tenant_id is required"


# ── Platform SaaS ────────────────────────────────────────────────────────────

@router.get("/api/v1/billing/config")
async def billing_config(request: Request):
    """Public-ish: whether platform Stripe Checkout is available."""
    conn = db.get_connection()
    keys = sb.platform_stripe_keys(conn)
    configured = bool(keys.get("secret_key"))
    return {
        "status": "ok",
        "platform_stripe": {
            "configured": configured,
            "publishable_key": keys.get("publishable_key") or "",
        },
    }


@router.get("/api/v1/billing/subscription")
async def get_subscription(request: Request, tenant_id: str | None = None):
    """Current Stripe subscription linkage for the active hotel."""
    get_current_user(request)
    tid, err = _resolve_billing_tenant(request, tenant_id)
    if err or not tid:
        return JSONResponse({"status": "error", "message": err or "tenant required"}, 400)

    conn = db.get_connection()
    sb.ensure_stripe_tables(conn)
    row = sb.get_stripe_customer_row(conn, tid)
    meta = _tenant_meta(tid)
    hotel_pay = sb.hotel_payment_settings(tid)
    return {
        "status": "ok",
        "platform_stripe_configured": sb.platform_stripe_configured(conn),
        "hotel_stripe_configured": sb.hotel_stripe_configured(tid),
        "hotel_publishable_key": hotel_pay.get("payment_publishable_key") or "",
        "tenant": meta,
        "subscription": row,
    }


@router.post("/api/v1/billing/checkout-session")
async def create_checkout_session(request: Request):
    """Start Stripe Checkout for SaaS plan signup / upgrade."""
    from auth.deps import reject_if_impersonating

    ctx = get_current_user(request)
    reject_if_impersonating(
        ctx,
        message="SaaS billing changes are blocked while impersonating a hotel",
    )
    try:
        body = await request.json()
        req = CheckoutRequest(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)

    tid, err = _resolve_billing_tenant(request, req.tenant_id)
    if err or not tid:
        return JSONResponse({"status": "error", "message": err or "tenant required"}, 400)

    # Hotel owners may self-serve; SuperAdmin always may.
    if not ctx.is_superadmin and ctx.isolation_id != tid and tid not in (ctx.assigned_properties or []):
        return JSONResponse({"status": "error", "message": "Permission denied"}, 403)

    meta = _tenant_meta(tid)
    if not meta:
        return JSONResponse({"status": "error", "message": "Hotel tenant not found"}, 404)

    plan_code = _normalize_plan(req.plan_code)
    cycle = (req.billing_cycle or "monthly").lower()
    if cycle not in ("monthly", "annual"):
        cycle = "monthly"

    success = req.success_url or f"{WEB_ORIGIN}/app/hotel/dashboard?billing=success"
    cancel = req.cancel_url or f"{WEB_ORIGIN}/app/hotel/settings/payments?billing=cancelled"

    try:
        result = sb.create_subscription_checkout(
            tenant_id=tid,
            email=meta.get("admin_email") or ctx.email or "",
            hotel_name=meta.get("hotel_name") or "Hotel",
            plan_code=plan_code,
            billing_cycle=cycle,
            success_url=success,
            cancel_url=cancel,
            trial_days=req.trial_days,
        )
    except ValueError as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    except Exception as e:
        log.exception("checkout-session failed")
        return JSONResponse({"status": "error", "message": f"Stripe error: {e}"}, 502)

    return {"status": "ok", **result}


@router.post("/api/v1/billing/portal-session")
async def create_portal_session(request: Request):
    """Open Stripe Customer Portal for the hotel's SaaS subscription."""
    from auth.deps import reject_if_impersonating

    ctx = get_current_user(request)
    reject_if_impersonating(
        ctx,
        message="Billing portal access is blocked while impersonating a hotel",
    )
    try:
        body = await request.json()
        req = PortalRequest(**(body or {}))
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)

    tid, err = _resolve_billing_tenant(request, req.tenant_id)
    if err or not tid:
        return JSONResponse({"status": "error", "message": err or "tenant required"}, 400)

    return_url = req.return_url or f"{WEB_ORIGIN}/app/hotel/settings/payments"
    try:
        result = sb.create_billing_portal(tenant_id=tid, return_url=return_url)
    except ValueError as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    except Exception as e:
        log.exception("portal-session failed")
        return JSONResponse({"status": "error", "message": f"Stripe error: {e}"}, 502)

    return {"status": "ok", **result}


@router.post("/api/v1/webhooks/stripe")
async def stripe_platform_webhook(request: Request):
    """Platform SaaS webhook — auto-renew, payment failures, suspend on lapse."""
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    conn = db.get_connection()
    keys = sb.platform_stripe_keys(conn)
    webhook_secret = keys.get("webhook_secret") or ""

    if not keys.get("secret_key"):
        return JSONResponse({"status": "error", "message": "Stripe not configured"}, 503)

    try:
        stripe = sb.get_stripe_client(keys["secret_key"])
        if webhook_secret:
            event = stripe.Webhook.construct_event(payload, sig, webhook_secret)
        else:
            # Dev fallback when webhook secret unset — parse JSON only
            import json
            event = json.loads(payload)
            if not isinstance(event, dict) or "type" not in event:
                return JSONResponse({"status": "error", "message": "Invalid event"}, 400)
    except Exception as e:
        log.warning("Webhook signature/parse failed: %s", e)
        return JSONResponse({"status": "error", "message": "Invalid webhook"}, 400)

    # stripe.Webhook returns a StripeObject; normalize to dict
    if hasattr(event, "to_dict"):
        event = event.to_dict()

    try:
        result = sb.handle_platform_webhook_event(event)
    except Exception:
        log.exception("Webhook handler failed")
        return JSONResponse({"status": "error", "message": "Handler failed"}, 500)

    return result


# ── Guest folio card payments ────────────────────────────────────────────────

@router.get("/api/v1/billing/hotel-payments")
async def hotel_payments_status(request: Request):
    """Whether the active hotel can charge guest cards via Stripe."""
    tid = require_hotel_tenant(get_current_user(request))
    settings = sb.hotel_payment_settings(tid)
    configured = sb.hotel_stripe_configured(tid)
    return {
        "status": "ok",
        "configured": configured,
        "gateway": settings.get("payment_gateway") or "none",
        "publishable_key": settings.get("payment_publishable_key") or "" if configured else "",
    }


@router.post("/api/v1/billing/folio/payment-intent")
@require_permission("folios.write")
async def create_folio_payment_intent(request: Request):
    """Create a PaymentIntent against the hotel's Stripe account for folio balance."""
    ctx = get_current_user(request)
    tid = require_hotel_tenant(ctx)
    try:
        body = await request.json()
        req = FolioPaymentIntentRequest(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)

    # Load folio balance from hospitality DB
    skill_db = db.get_skill_db("erpclaw") or db.get_skill_db("hospitalityclaw")
    if not skill_db:
        return JSONResponse({"status": "error", "message": "Hotel database unavailable"}, 503)

    try:
        folio = skill_db.execute(
            """SELECT f.id, f.balance, f.status, f.guest_id, h.currency,
                      g.first_name, g.last_name
               FROM hospitalityclaw_folio f
               JOIN hospitalityclaw_reservation r ON r.id = f.reservation_id
               LEFT JOIN hospitalityclaw_hotel h ON h.id = r.hotel_id
               LEFT JOIN hospitalityclaw_guest g ON g.id = f.guest_id
               WHERE f.id = ? AND f.tenant_id = ?""",
            (req.folio_id, tid),
        ).fetchone()
        if not folio:
            return JSONResponse({"status": "error", "message": "Folio not found"}, 404)
        status = folio[2] if isinstance(folio, tuple) else folio["status"]
        if status != "open":
            return JSONResponse({"status": "error", "message": "Folio is not open"}, 400)
        balance = folio[1] if isinstance(folio, tuple) else folio["balance"]
        currency = (
            (req.currency or (folio[4] if isinstance(folio, tuple) else folio["currency"]) or "USD")
        ).upper()
        amount = req.amount or str(balance or "0")
        first = folio[5] if isinstance(folio, tuple) else folio["first_name"]
        last = folio[6] if isinstance(folio, tuple) else folio["last_name"]
        guest_name = f"{first or ''} {last or ''}".strip() or None
    finally:
        skill_db.close()

    try:
        result = sb.create_folio_payment_intent(
            tenant_id=tid,
            folio_id=req.folio_id,
            amount=amount,
            currency=currency,
            guest_name=guest_name,
            description=req.description,
        )
    except ValueError as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    except Exception as e:
        log.exception("folio payment-intent failed")
        return JSONResponse({"status": "error", "message": f"Stripe error: {e}"}, 502)

    return {"status": "ok", **result}


@router.post("/api/v1/billing/folio/confirm")
@require_permission("folios.write")
async def confirm_folio_payment(request: Request):
    """After client confirms the PaymentIntent, settle the folio and post GL."""
    ctx = get_current_user(request)
    tid = require_hotel_tenant(ctx)
    try:
        body = await request.json()
        req = FolioConfirmRequest(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)

    try:
        intent = sb.retrieve_folio_payment_intent(tid, req.payment_intent_id)
    except ValueError as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    except Exception as e:
        log.exception("retrieve payment intent failed")
        return JSONResponse({"status": "error", "message": f"Stripe error: {e}"}, 502)

    intent_status = intent.get("status") if isinstance(intent, dict) else getattr(intent, "status", None)
    meta = intent.get("metadata") if isinstance(intent, dict) else getattr(intent, "metadata", {}) or {}
    if hasattr(meta, "to_dict"):
        meta = meta.to_dict()

    if str(meta.get("tenant_id") or "") != tid:
        return JSONResponse({"status": "error", "message": "PaymentIntent tenant mismatch"}, 403)
    if str(meta.get("folio_id") or "") != req.folio_id:
        return JSONResponse({"status": "error", "message": "PaymentIntent folio mismatch"}, 400)
    if intent_status != "succeeded":
        return JSONResponse(
            {"status": "error", "message": f"Payment not succeeded (status={intent_status})"},
            400,
        )

    # Idempotency: skip settle if a payment transaction already references this PI
    skill_db = db.get_skill_db("erpclaw") or db.get_skill_db("hospitalityclaw")
    if skill_db:
        try:
            existing = skill_db.execute(
                """SELECT id FROM hospitalityclaw_transaction
                   WHERE tenant_id = ? AND folio_id = ?
                     AND reference = ?
                   LIMIT 1""",
                (tid, req.folio_id, req.payment_intent_id),
            ).fetchone()
            if existing:
                return {
                    "status": "ok",
                    "settled": True,
                    "already_posted": True,
                    "payment_intent_id": req.payment_intent_id,
                    "charge_id": existing[0] if isinstance(existing, tuple) else existing["id"],
                }
        finally:
            skill_db.close()

    settle = await execute_skill(
        "hospitalityclaw",
        "settle-folio",
        {
            "folio_id": req.folio_id,
            "payment_method": "card",
            "reference": req.payment_intent_id,
            "description": f"Card payment ({req.payment_intent_id})",
        },
        tenant_id=tid,
    )
    if settle.get("status") != "ok":
        return JSONResponse(
            {
                "status": "error",
                "message": settle.get("message") or settle.get("error") or "Settle failed",
                "detail": settle,
                "payment_captured": True,
                "payment_intent_id": req.payment_intent_id,
            },
            502,
        )

    return {
        "status": "ok",
        "settled": True,
        "payment_intent_id": req.payment_intent_id,
        "amount": settle.get("amount"),
        "charge_id": settle.get("charge_id"),
        "gl_posted": settle.get("gl_posted"),
        "message": "Card payment captured and folio settled to erpclaw GL",
    }


def _folio_row(skill_db, folio_id: str, tid: str):
    return skill_db.execute(
        """SELECT f.id, f.balance, f.status, f.guest_id, f.reservation_id,
                  h.currency, h.settings_json,
                  g.first_name, g.last_name,
                  r.rate, r.check_in, r.check_out, r.status AS reservation_status,
                  r.notes AS reservation_notes
           FROM hospitalityclaw_folio f
           JOIN hospitalityclaw_reservation r ON r.id = f.reservation_id
           LEFT JOIN hospitalityclaw_hotel h ON h.id = r.hotel_id
           LEFT JOIN hospitalityclaw_guest g ON g.id = f.guest_id
           WHERE f.id = ? AND f.tenant_id = ?""",
        (folio_id, tid),
    ).fetchone()


def _row_field(row, key: str, idx: int):
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


def _suggest_deposit_amount(settings_raw, rate, check_in, check_out) -> str:
    from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
    import json as _json

    try:
        data = _json.loads(settings_raw) if isinstance(settings_raw, str) else (settings_raw or {})
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    payments = data.get("payments") if isinstance(data.get("payments"), dict) else {}
    nights_raw = (
        payments.get("deposit_nights")
        or data.get("deposit_nights")
        or 1
    )
    pct_raw = payments.get("deposit_percent") or data.get("deposit_percent")
    fixed_raw = payments.get("deposit_amount") or data.get("deposit_amount")

    try:
        nightly = Decimal(str(rate or "0"))
    except (InvalidOperation, ValueError):
        nightly = Decimal("0")

    stay_nights = 1
    try:
        from datetime import date as _date
        cin = _date.fromisoformat(str(check_in)[:10])
        cout = _date.fromisoformat(str(check_out)[:10])
        stay_nights = max(1, (cout - cin).days)
    except Exception:
        stay_nights = 1

    if fixed_raw not in (None, ""):
        try:
            return str(Decimal(str(fixed_raw)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        except Exception:
            pass
    if pct_raw not in (None, ""):
        try:
            total = nightly * Decimal(stay_nights)
            pct = Decimal(str(pct_raw))
            return str((total * pct / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        except Exception:
            pass
    try:
        nights = max(1, min(stay_nights, int(float(nights_raw))))
    except Exception:
        nights = 1
    return str((nightly * Decimal(nights)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


@router.get("/api/v1/billing/folio/deposit-quote")
@require_permission("folios.write")
async def quote_folio_deposit(request: Request, folio_id: str):
    """Suggested deposit/guarantee amount for a folio based on hotel policy."""
    tid = require_hotel_tenant(get_current_user(request))
    skill_db = db.get_skill_db("erpclaw") or db.get_skill_db("hospitalityclaw")
    if not skill_db:
        return JSONResponse({"status": "error", "message": "Hotel database unavailable"}, 503)
    try:
        folio = _folio_row(skill_db, folio_id, tid)
        if not folio:
            return JSONResponse({"status": "error", "message": "Folio not found"}, 404)
        amount = _suggest_deposit_amount(
            _row_field(folio, "settings_json", 6),
            _row_field(folio, "rate", 9),
            _row_field(folio, "check_in", 10),
            _row_field(folio, "check_out", 11),
        )
        currency = (_row_field(folio, "currency", 5) or "USD").upper()
        return {
            "status": "ok",
            "folio_id": folio_id,
            "reservation_id": _row_field(folio, "reservation_id", 4),
            "amount": amount,
            "currency": currency,
            "reservation_status": _row_field(folio, "reservation_status", 12),
        }
    finally:
        skill_db.close()


@router.post("/api/v1/billing/folio/deposit-intent")
@require_permission("folios.write")
async def create_folio_deposit_intent(request: Request):
    """Create a PaymentIntent for a booking deposit / card guarantee."""
    ctx = get_current_user(request)
    tid = require_hotel_tenant(ctx)
    try:
        body = await request.json()
        req = FolioDepositIntentRequest(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)

    skill_db = db.get_skill_db("erpclaw") or db.get_skill_db("hospitalityclaw")
    if not skill_db:
        return JSONResponse({"status": "error", "message": "Hotel database unavailable"}, 503)

    try:
        folio = _folio_row(skill_db, req.folio_id, tid)
        if not folio:
            return JSONResponse({"status": "error", "message": "Folio not found"}, 404)
        status = _row_field(folio, "status", 2)
        if status != "open":
            return JSONResponse({"status": "error", "message": "Folio is not open"}, 400)
        currency = (
            req.currency or _row_field(folio, "currency", 5) or "USD"
        ).upper()
        amount = req.amount or _suggest_deposit_amount(
            _row_field(folio, "settings_json", 6),
            _row_field(folio, "rate", 9),
            _row_field(folio, "check_in", 10),
            _row_field(folio, "check_out", 11),
        )
        first = _row_field(folio, "first_name", 7)
        last = _row_field(folio, "last_name", 8)
        guest_name = f"{first or ''} {last or ''}".strip() or None
        reservation_id = _row_field(folio, "reservation_id", 4)
    finally:
        skill_db.close()

    try:
        result = sb.create_folio_payment_intent(
            tenant_id=tid,
            folio_id=req.folio_id,
            amount=amount,
            currency=currency,
            guest_name=guest_name,
            description=req.description or f"Deposit / guarantee ({req.folio_id})",
            kind="guest_deposit",
            reservation_id=str(reservation_id or ""),
        )
    except ValueError as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    except Exception as e:
        log.exception("folio deposit-intent failed")
        return JSONResponse({"status": "error", "message": f"Stripe error: {e}"}, 502)

    return {"status": "ok", **result}


@router.post("/api/v1/billing/folio/deposit-confirm")
@require_permission("folios.write")
async def confirm_folio_deposit(request: Request):
    """Post a captured deposit PaymentIntent as a folio payment (prepaid credit)."""
    ctx = get_current_user(request)
    tid = require_hotel_tenant(ctx)
    try:
        body = await request.json()
        req = FolioDepositConfirmRequest(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)

    try:
        intent = sb.retrieve_folio_payment_intent(tid, req.payment_intent_id)
    except ValueError as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    except Exception as e:
        log.exception("retrieve deposit intent failed")
        return JSONResponse({"status": "error", "message": f"Stripe error: {e}"}, 502)

    intent_status = intent.get("status") if isinstance(intent, dict) else getattr(intent, "status", None)
    meta = intent.get("metadata") if isinstance(intent, dict) else getattr(intent, "metadata", {}) or {}
    if hasattr(meta, "to_dict"):
        meta = meta.to_dict()
    amount_cents = intent.get("amount") if isinstance(intent, dict) else getattr(intent, "amount", 0)
    currency = (
        intent.get("currency") if isinstance(intent, dict) else getattr(intent, "currency", "usd")
    ) or "usd"

    if str(meta.get("tenant_id") or "") != tid:
        return JSONResponse({"status": "error", "message": "PaymentIntent tenant mismatch"}, 403)
    if str(meta.get("folio_id") or "") != req.folio_id:
        return JSONResponse({"status": "error", "message": "PaymentIntent folio mismatch"}, 400)
    if str(meta.get("kind") or "") not in ("guest_deposit", "guest_folio"):
        return JSONResponse({"status": "error", "message": "Not a deposit PaymentIntent"}, 400)
    if intent_status != "succeeded":
        return JSONResponse(
            {"status": "error", "message": f"Payment not succeeded (status={intent_status})"},
            400,
        )

    # Convert cents → major units (zero-decimal currencies already handled by Stripe amount)
    from decimal import Decimal, ROUND_HALF_UP

    zero_decimal = {"bif", "clp", "djf", "gnf", "jpy", "kmf", "krw", "mga", "pyg", "rwf", "ugx", "vnd", "vuv", "xaf", "xof", "xpf"}
    try:
        cents = Decimal(str(amount_cents or 0))
        if str(currency).lower() in zero_decimal:
            amount = str(cents.quantize(Decimal("1")))
        else:
            amount = str((cents / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except Exception:
        amount = "0"

    skill_db = db.get_skill_db("erpclaw") or db.get_skill_db("hospitalityclaw")
    if skill_db:
        try:
            existing = skill_db.execute(
                """SELECT id FROM hospitalityclaw_transaction
                   WHERE tenant_id = ? AND folio_id = ?
                     AND reference = ?
                   LIMIT 1""",
                (tid, req.folio_id, req.payment_intent_id),
            ).fetchone()
            if existing:
                return {
                    "status": "ok",
                    "posted": True,
                    "already_posted": True,
                    "payment_intent_id": req.payment_intent_id,
                    "charge_id": existing[0] if isinstance(existing, tuple) else existing["id"],
                    "amount": amount,
                }
        finally:
            skill_db.close()

    posted = await execute_skill(
        "hospitalityclaw",
        "add-folio-charge",
        {
            "folio_id": req.folio_id,
            "charge_type": "payment",
            "payment_method": "card",
            "amount": amount,
            "reference": req.payment_intent_id,
            "description": f"Deposit / guarantee ({req.payment_intent_id})",
        },
        tenant_id=tid,
    )
    if posted.get("status") != "ok":
        return JSONResponse(
            {
                "status": "error",
                "message": posted.get("message") or posted.get("error") or "Deposit post failed",
                "detail": posted,
                "payment_captured": True,
                "payment_intent_id": req.payment_intent_id,
            },
            502,
        )

    # Annotate reservation notes
    reservation_id = str(meta.get("reservation_id") or "")
    if reservation_id:
        note_db = db.get_skill_db("erpclaw") or db.get_skill_db("hospitalityclaw")
        if note_db:
            try:
                row = note_db.execute(
                    "SELECT notes FROM hospitalityclaw_reservation WHERE id = ? AND tenant_id = ?",
                    (reservation_id, tid),
                ).fetchone()
                existing_notes = ""
                if row:
                    existing_notes = (
                        row["notes"] if hasattr(row, "keys") else (row[0] if row else "")
                    ) or ""
                marker = f"[Deposit paid {amount} {str(currency).upper()} via {req.payment_intent_id}]"
                if marker not in existing_notes:
                    notes = f"{existing_notes}\n{marker}".strip() if existing_notes else marker
                    note_db.execute(
                        """UPDATE hospitalityclaw_reservation
                           SET notes = ?, updated_at = CURRENT_TIMESTAMP
                           WHERE id = ? AND tenant_id = ?""",
                        (notes, reservation_id, tid),
                    )
                    note_db.commit()
            except Exception:
                log.exception("deposit note update failed")
            finally:
                note_db.close()

    return {
        "status": "ok",
        "posted": True,
        "kind": "guest_deposit",
        "payment_intent_id": req.payment_intent_id,
        "amount": amount,
        "charge_id": posted.get("charge_id"),
        "gl_posted": posted.get("gl_posted"),
        "message": "Deposit captured and posted as folio payment credit",
    }
