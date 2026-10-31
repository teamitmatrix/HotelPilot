"""Stripe helpers for SaaS subscriptions and hotel guest folio charges.

Two key scopes:
  - Platform (HotelPilot SaaS): SuperAdmin Stripe keys → tenant subscriptions
  - Hotel: per-property keys in hospitality settings_json → guest PaymentIntents
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import db
from db_access import DbConn

log = logging.getLogger("webclaw.stripe")

PLAN_TO_TIER = {"starter": "starter", "business": "professional", "enterprise": "enterprise"}
TIER_TO_PLAN = {
    "starter": "starter",
    "professional": "business",
    "business": "business",
    "enterprise": "enterprise",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_value(row, key: str, idx: int = 0):
    if row is None:
        return None
    if hasattr(row, "keys"):
        try:
            return row[key]
        except (KeyError, IndexError):
            return None
    try:
        return row[idx]
    except (IndexError, TypeError):
        return None


STRIPE_DDL = """
CREATE TABLE IF NOT EXISTS webclaw_stripe_customer (
    tenant_id               TEXT PRIMARY KEY,
    stripe_customer_id      TEXT NOT NULL UNIQUE,
    stripe_subscription_id  TEXT,
    stripe_price_id         TEXT,
    plan_code               TEXT,
    status                  TEXT NOT NULL DEFAULT 'incomplete',
    current_period_end      TEXT,
    cancel_at_period_end    INTEGER NOT NULL DEFAULT 0,
    updated_at              TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_stripe_customer_sub
    ON webclaw_stripe_customer(stripe_subscription_id);
CREATE INDEX IF NOT EXISTS idx_stripe_customer_cust
    ON webclaw_stripe_customer(stripe_customer_id);

CREATE TABLE IF NOT EXISTS webclaw_stripe_webhook_event (
    id              TEXT PRIMARY KEY,
    event_type      TEXT NOT NULL,
    processed_at    TEXT NOT NULL,
    detail          TEXT
);
"""


def ensure_stripe_tables(conn: DbConn) -> None:
    """Tenant Stripe linkage + webhook idempotency."""
    from db_access import run_ddl, table_columns

    run_ddl(conn, STRIPE_DDL)
    # Stripe Price IDs cached on plan catalog
    cols = table_columns(conn, "webclaw_subscription_plan")
    for col, ddl in (
        ("stripe_product_id", "ALTER TABLE webclaw_subscription_plan ADD COLUMN stripe_product_id TEXT"),
        ("stripe_price_monthly_id", "ALTER TABLE webclaw_subscription_plan ADD COLUMN stripe_price_monthly_id TEXT"),
        ("stripe_price_annual_id", "ALTER TABLE webclaw_subscription_plan ADD COLUMN stripe_price_annual_id TEXT"),
    ):
        if cols and col not in cols:
            try:
                conn.execute(ddl)
            except Exception:
                pass
    conn.commit()


def platform_stripe_keys(conn: DbConn | None = None) -> dict[str, str]:
    """Resolve platform Stripe credentials: env vars override stored settings."""
    secret = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    publishable = (os.environ.get("STRIPE_PUBLISHABLE_KEY") or "").strip()
    webhook = (os.environ.get("STRIPE_WEBHOOK_SECRET") or "").strip()

    if not (secret and publishable and webhook) or not secret:
        try:
            from admin_ops import _load_settings

            c = conn or db.get_connection()
            settings = _load_settings(c)
            stripe = (settings.get("integrations") or {}).get("stripe") or {}
            secret = secret or str(stripe.get("secret_key") or "").strip()
            publishable = publishable or str(stripe.get("publishable_key") or "").strip()
            webhook = webhook or str(stripe.get("webhook_secret") or "").strip()
        except Exception:
            pass

    return {
        "secret_key": secret,
        "publishable_key": publishable,
        "webhook_secret": webhook,
    }


def platform_stripe_configured(conn: DbConn | None = None) -> bool:
    keys = platform_stripe_keys(conn)
    return bool(keys.get("secret_key"))


def get_stripe_client(secret_key: str | None = None):
    """Return a configured Stripe module, or raise ValueError if unavailable."""
    key = (secret_key or "").strip()
    if not key:
        raise ValueError("Stripe secret key is not configured")
    try:
        import stripe
    except ImportError as exc:
        raise ValueError("stripe package is not installed") from exc
    stripe.api_key = key
    return stripe


def amount_to_cents(amount: str | float | Decimal, currency: str = "USD") -> int:
    """Convert a decimal money amount to Stripe's smallest currency unit."""
    zero_decimal = {
        "bif", "clp", "djf", "gnf", "jpy", "kmf", "krw", "mga", "pyg",
        "rwf", "ugx", "vnd", "vuv", "xaf", "xof", "xpf",
    }
    value = Decimal(str(amount))
    if currency.lower() in zero_decimal:
        return int(value.to_integral_value(rounding=ROUND_HALF_UP))
    return int((value * 100).to_integral_value(rounding=ROUND_HALF_UP))


def hotel_payment_settings(tenant_id: str) -> dict[str, str]:
    """Load Stripe keys for a hotel tenant from hospitality settings_json."""
    skill_db = db.get_skill_db("erpclaw") or db.get_skill_db("hospitalityclaw")
    if not skill_db:
        return {}
    try:
        row = skill_db.execute(
            """SELECT settings_json FROM hospitalityclaw_hotel
               WHERE tenant_id = ? ORDER BY created_at ASC LIMIT 1""",
            (tenant_id,),
        ).fetchone()
        if not row:
            return {}
        raw = _row_value(row, "settings_json", 0) or "{}"
        data = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if not isinstance(data, dict):
            return {}
        return {
            "payment_gateway": str(data.get("payment_gateway") or "none"),
            "payment_publishable_key": str(data.get("payment_publishable_key") or "").strip(),
            "payment_secret_key": str(data.get("payment_secret_key") or "").strip(),
            "payment_webhook_secret": str(data.get("payment_webhook_secret") or "").strip(),
        }
    except Exception:
        return {}
    finally:
        skill_db.close()


def hotel_stripe_configured(tenant_id: str) -> bool:
    s = hotel_payment_settings(tenant_id)
    return (
        s.get("payment_gateway") == "stripe"
        and bool(s.get("payment_secret_key"))
        and bool(s.get("payment_publishable_key"))
    )


def _upsert_stripe_customer_row(
    conn: DbConn,
    *,
    tenant_id: str,
    stripe_customer_id: str,
    stripe_subscription_id: str | None = None,
    stripe_price_id: str | None = None,
    plan_code: str | None = None,
    status: str | None = None,
    current_period_end: str | None = None,
    cancel_at_period_end: bool | None = None,
) -> None:
    ensure_stripe_tables(conn)
    existing = conn.execute(
        "SELECT * FROM webclaw_stripe_customer WHERE tenant_id = ?",
        (tenant_id,),
    ).fetchone()
    if existing:
        updates = ["stripe_customer_id = ?", "updated_at = ?"]
        params: list[Any] = [stripe_customer_id, _now()]
        if stripe_subscription_id is not None:
            updates.append("stripe_subscription_id = ?")
            params.append(stripe_subscription_id)
        if stripe_price_id is not None:
            updates.append("stripe_price_id = ?")
            params.append(stripe_price_id)
        if plan_code is not None:
            updates.append("plan_code = ?")
            params.append(plan_code)
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if current_period_end is not None:
            updates.append("current_period_end = ?")
            params.append(current_period_end)
        if cancel_at_period_end is not None:
            updates.append("cancel_at_period_end = ?")
            params.append(1 if cancel_at_period_end else 0)
        params.append(tenant_id)
        conn.execute(
            f"UPDATE webclaw_stripe_customer SET {', '.join(updates)} WHERE tenant_id = ?",
            params,
        )
    else:
        conn.execute(
            """INSERT INTO webclaw_stripe_customer
               (tenant_id, stripe_customer_id, stripe_subscription_id, stripe_price_id,
                plan_code, status, current_period_end, cancel_at_period_end, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                tenant_id,
                stripe_customer_id,
                stripe_subscription_id,
                stripe_price_id,
                plan_code,
                status or "incomplete",
                current_period_end,
                1 if cancel_at_period_end else 0,
                _now(),
            ),
        )
    conn.commit()


def get_stripe_customer_row(conn: DbConn, tenant_id: str) -> dict | None:
    ensure_stripe_tables(conn)
    row = conn.execute(
        "SELECT * FROM webclaw_stripe_customer WHERE tenant_id = ?",
        (tenant_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "tenant_id": _row_value(row, "tenant_id", 0),
        "stripe_customer_id": _row_value(row, "stripe_customer_id", 1),
        "stripe_subscription_id": _row_value(row, "stripe_subscription_id", 2),
        "stripe_price_id": _row_value(row, "stripe_price_id", 3),
        "plan_code": _row_value(row, "plan_code", 4),
        "status": _row_value(row, "status", 5),
        "current_period_end": _row_value(row, "current_period_end", 6),
        "cancel_at_period_end": bool(_row_value(row, "cancel_at_period_end", 7)),
        "updated_at": _row_value(row, "updated_at", 8),
    }


def ensure_platform_customer(
    stripe,
    conn: DbConn,
    *,
    tenant_id: str,
    email: str,
    hotel_name: str,
) -> str:
    """Return Stripe customer id for a hotel tenant, creating one if needed."""
    row = get_stripe_customer_row(conn, tenant_id)
    if row and row.get("stripe_customer_id"):
        return row["stripe_customer_id"]

    customer = stripe.Customer.create(
        email=email,
        name=hotel_name,
        metadata={"tenant_id": tenant_id, "hotel_name": hotel_name},
    )
    _upsert_stripe_customer_row(
        conn,
        tenant_id=tenant_id,
        stripe_customer_id=customer["id"],
        status="incomplete",
    )
    return customer["id"]


def ensure_plan_price(
    stripe,
    conn: DbConn,
    plan_row,
    billing_cycle: str = "monthly",
) -> str:
    """Ensure a Stripe Product/Price exists for the plan; return price id."""
    from admin_platform import ensure_subscription_plans

    ensure_subscription_plans(conn)
    ensure_stripe_tables(conn)

    code = _row_value(plan_row, "code", 1)
    name = _row_value(plan_row, "name", 2) or code
    cycle = (billing_cycle or _row_value(plan_row, "billing_cycle", 3) or "monthly").lower()
    if cycle not in ("monthly", "annual"):
        cycle = "monthly"

    price_col = "stripe_price_monthly_id" if cycle == "monthly" else "stripe_price_annual_id"
    existing_price = None
    if hasattr(plan_row, "keys") and price_col in plan_row.keys():
        existing_price = plan_row[price_col]
    if existing_price:
        return str(existing_price)

    # Re-read after possible migration
    refreshed = conn.execute(
        "SELECT * FROM webclaw_subscription_plan WHERE code = ?",
        (code,),
    ).fetchone()
    if refreshed and hasattr(refreshed, "keys") and refreshed[price_col]:
        return str(refreshed[price_col])

    amount_raw = (
        _row_value(refreshed or plan_row, "monthly_price", 4)
        if cycle == "monthly"
        else _row_value(refreshed or plan_row, "annual_price", 5)
    )
    unit_amount = amount_to_cents(amount_raw or "0", "USD")
    if unit_amount <= 0:
        raise ValueError(f"Plan {code} has no billable {cycle} price")

    product_id = None
    if refreshed and hasattr(refreshed, "keys"):
        product_id = refreshed["stripe_product_id"] if "stripe_product_id" in refreshed.keys() else None

    if not product_id:
        product = stripe.Product.create(
            name=f"HotelPilot {name}",
            metadata={"plan_code": code},
        )
        product_id = product["id"]
        conn.execute(
            "UPDATE webclaw_subscription_plan SET stripe_product_id = ?, updated_at = ? WHERE code = ?",
            (product_id, _now(), code),
        )

    price = stripe.Price.create(
        product=product_id,
        unit_amount=unit_amount,
        currency="usd",
        recurring={"interval": "month" if cycle == "monthly" else "year"},
        metadata={"plan_code": code, "billing_cycle": cycle},
    )
    conn.execute(
        f"UPDATE webclaw_subscription_plan SET {price_col} = ?, updated_at = ? WHERE code = ?",
        (price["id"], _now(), code),
    )
    conn.commit()
    return price["id"]


def create_subscription_checkout(
    *,
    tenant_id: str,
    email: str,
    hotel_name: str,
    plan_code: str,
    billing_cycle: str = "monthly",
    success_url: str,
    cancel_url: str,
    trial_days: int | None = None,
) -> dict:
    """Create a Stripe Checkout Session for SaaS subscription billing."""
    conn = db.get_connection()
    keys = platform_stripe_keys(conn)
    if not keys.get("secret_key"):
        raise ValueError("Platform Stripe is not configured")

    from admin_platform import ensure_subscription_plans, _get_plan

    ensure_subscription_plans(conn)
    plan = _get_plan(conn, plan_code)
    if not plan:
        raise ValueError(f"Unknown plan: {plan_code}")

    stripe = get_stripe_client(keys["secret_key"])
    customer_id = ensure_platform_customer(
        stripe, conn, tenant_id=tenant_id, email=email, hotel_name=hotel_name,
    )
    price_id = ensure_plan_price(stripe, conn, plan, billing_cycle=billing_cycle)

    subscription_data: dict[str, Any] = {
        "metadata": {
            "tenant_id": tenant_id,
            "plan_code": plan_code,
            "billing_cycle": billing_cycle,
        },
    }
    if trial_days and trial_days > 0:
        subscription_data["trial_period_days"] = int(trial_days)

    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        client_reference_id=tenant_id,
        metadata={
            "tenant_id": tenant_id,
            "plan_code": plan_code,
            "billing_cycle": billing_cycle,
        },
        subscription_data=subscription_data,
        allow_promotion_codes=True,
    )
    _upsert_stripe_customer_row(
        conn,
        tenant_id=tenant_id,
        stripe_customer_id=customer_id,
        stripe_price_id=price_id,
        plan_code=plan_code,
        status="incomplete",
    )
    return {
        "checkout_url": session["url"],
        "session_id": session["id"],
        "customer_id": customer_id,
        "price_id": price_id,
        "plan_code": plan_code,
    }


def create_billing_portal(*, tenant_id: str, return_url: str) -> dict:
    """Stripe Customer Portal for plan changes / payment method updates."""
    conn = db.get_connection()
    keys = platform_stripe_keys(conn)
    row = get_stripe_customer_row(conn, tenant_id)
    if not row or not row.get("stripe_customer_id"):
        raise ValueError("No Stripe customer for this hotel")
    stripe = get_stripe_client(keys["secret_key"])
    session = stripe.billing_portal.Session.create(
        customer=row["stripe_customer_id"],
        return_url=return_url,
    )
    return {"portal_url": session["url"]}


def create_folio_payment_intent(
    *,
    tenant_id: str,
    folio_id: str,
    amount: str | float | Decimal,
    currency: str,
    guest_name: str | None = None,
    description: str | None = None,
    kind: str = "guest_folio",
    reservation_id: str | None = None,
) -> dict:
    """Create a PaymentIntent using the hotel's own Stripe secret key."""
    settings = hotel_payment_settings(tenant_id)
    if settings.get("payment_gateway") != "stripe":
        raise ValueError("Stripe is not the configured payment gateway for this hotel")
    secret = settings.get("payment_secret_key") or ""
    publishable = settings.get("payment_publishable_key") or ""
    if not secret or not publishable:
        raise ValueError("Hotel Stripe keys are incomplete")

    cents = amount_to_cents(amount, currency)
    if cents <= 0:
        raise ValueError("Payment amount must be greater than zero")

    meta_kind = kind if kind in ("guest_folio", "guest_deposit") else "guest_folio"
    stripe = get_stripe_client(secret)
    intent = stripe.PaymentIntent.create(
        amount=cents,
        currency=currency.lower(),
        automatic_payment_methods={"enabled": True},
        description=description
        or (
            f"Deposit for folio {folio_id}"
            if meta_kind == "guest_deposit"
            else f"Folio {folio_id} settlement"
        ),
        metadata={
            "tenant_id": tenant_id,
            "folio_id": folio_id,
            "kind": meta_kind,
            "guest_name": (guest_name or "")[:120],
            "reservation_id": (reservation_id or "")[:80],
        },
    )
    return {
        "client_secret": intent["client_secret"],
        "payment_intent_id": intent["id"],
        "publishable_key": publishable,
        "amount": str(amount),
        "amount_cents": cents,
        "currency": currency.lower(),
        "kind": meta_kind,
    }


def retrieve_folio_payment_intent(tenant_id: str, payment_intent_id: str) -> dict:
    settings = hotel_payment_settings(tenant_id)
    secret = settings.get("payment_secret_key") or ""
    stripe = get_stripe_client(secret)
    intent = stripe.PaymentIntent.retrieve(payment_intent_id)
    return intent


def set_tenant_status(tenant_id: str, status: str) -> None:
    """Update pms_tenant.status and mirror staff account availability."""
    from admin_platform import _set_hotel_users_status

    erp = db.get_skill_db("erpclaw")
    if not erp:
        return
    try:
        erp.execute(
            """UPDATE pms_tenant SET status = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ? OR company_id = ?""",
            (status, tenant_id, tenant_id),
        )
        erp.commit()
    except Exception:
        log.exception("Failed to set tenant status %s → %s", tenant_id, status)
    finally:
        erp.close()

    if status == "active":
        _set_hotel_users_status(tenant_id, "active")
    elif status in ("suspended", "cancelled"):
        _set_hotel_users_status(tenant_id, "disabled")


def set_tenant_plan(tenant_id: str, plan_code: str) -> None:
    """Map plan code → subscription_tier on pms_tenant."""
    tier = PLAN_TO_TIER.get(plan_code, plan_code)
    if tier not in ("starter", "professional", "enterprise"):
        tier = "starter"
    erp = db.get_skill_db("erpclaw")
    if not erp:
        return
    try:
        erp.execute(
            """UPDATE pms_tenant SET subscription_tier = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ? OR company_id = ?""",
            (tier, tenant_id, tenant_id),
        )
        erp.commit()
    except Exception:
        log.exception("Failed to set tenant plan %s → %s", tenant_id, plan_code)
    finally:
        erp.close()


def _period_end_iso(subscription: dict) -> str | None:
    ts = subscription.get("current_period_end")
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except Exception:
        return None


def _plan_code_from_subscription(subscription: dict) -> str | None:
    meta = subscription.get("metadata") or {}
    if meta.get("plan_code"):
        return str(meta["plan_code"])
    items = (subscription.get("items") or {}).get("data") or []
    if items:
        price = items[0].get("price") or {}
        price_meta = price.get("metadata") or {}
        if price_meta.get("plan_code"):
            return str(price_meta["plan_code"])
    return None


def apply_subscription_state(
    conn: DbConn,
    *,
    tenant_id: str,
    subscription: dict,
    actor: str = "stripe_webhook",
) -> None:
    """Sync local tenant + stripe_customer from a Stripe Subscription object."""
    from admin_platform import _record_subscription_event

    status = str(subscription.get("status") or "incomplete")
    plan_code = _plan_code_from_subscription(subscription) or "starter"
    price_id = None
    items = (subscription.get("items") or {}).get("data") or []
    if items:
        price_id = (items[0].get("price") or {}).get("id")

    customer_id = subscription.get("customer")
    if isinstance(customer_id, dict):
        customer_id = customer_id.get("id")

    _upsert_stripe_customer_row(
        conn,
        tenant_id=tenant_id,
        stripe_customer_id=str(customer_id or ""),
        stripe_subscription_id=subscription.get("id"),
        stripe_price_id=price_id,
        plan_code=plan_code,
        status=status,
        current_period_end=_period_end_iso(subscription),
        cancel_at_period_end=bool(subscription.get("cancel_at_period_end")),
    )

    if status in ("active", "trialing"):
        set_tenant_plan(tenant_id, plan_code)
        set_tenant_status(tenant_id, "active")
        _record_subscription_event(
            conn, tenant_id, "stripe_subscription_active",
            detail=json.dumps({"status": status, "plan_code": plan_code, "subscription_id": subscription.get("id")}),
            actor_email=actor,
        )
    elif status in ("past_due", "unpaid"):
        _record_subscription_event(
            conn, tenant_id, "stripe_payment_failed",
            detail=json.dumps({"status": status, "subscription_id": subscription.get("id")}),
            actor_email=actor,
        )
        try:
            from admin_platform import _upsert_lifecycle

            _upsert_lifecycle(conn, tenant_id, "past_due" if status == "past_due" else "suspended")
        except Exception:
            pass
        # Past-due keeps access briefly; unpaid suspends.
        if status == "unpaid":
            set_tenant_status(tenant_id, "suspended")
            _record_subscription_event(
                conn, tenant_id, "stripe_hotel_suspended",
                detail="Subscription unpaid",
                actor_email=actor,
            )
    elif status in ("canceled", "incomplete_expired"):
        set_tenant_status(tenant_id, "suspended")
        _record_subscription_event(
            conn, tenant_id, "stripe_hotel_suspended",
            detail=json.dumps({"status": status, "subscription_id": subscription.get("id")}),
            actor_email=actor,
        )


def resolve_tenant_from_stripe_object(conn: DbConn, obj: dict) -> str | None:
    """Find tenant_id from metadata, client_reference_id, or customer map."""
    meta = obj.get("metadata") or {}
    if meta.get("tenant_id"):
        return str(meta["tenant_id"])
    if obj.get("client_reference_id"):
        return str(obj["client_reference_id"])

    customer_id = obj.get("customer")
    if isinstance(customer_id, dict):
        customer_id = customer_id.get("id")
    if customer_id:
        ensure_stripe_tables(conn)
        row = conn.execute(
            "SELECT tenant_id FROM webclaw_stripe_customer WHERE stripe_customer_id = ?",
            (str(customer_id),),
        ).fetchone()
        if row:
            return str(_row_value(row, "tenant_id", 0))

    sub_id = obj.get("subscription") or obj.get("id")
    if obj.get("object") == "subscription":
        sub_id = obj.get("id")
    if sub_id and not str(sub_id).startswith("cus_"):
        ensure_stripe_tables(conn)
        row = conn.execute(
            "SELECT tenant_id FROM webclaw_stripe_customer WHERE stripe_subscription_id = ?",
            (str(sub_id),),
        ).fetchone()
        if row:
            return str(_row_value(row, "tenant_id", 0))
    return None


def webhook_already_processed(conn: DbConn, event_id: str) -> bool:
    ensure_stripe_tables(conn)
    row = conn.execute(
        "SELECT 1 FROM webclaw_stripe_webhook_event WHERE id = ?",
        (event_id,),
    ).fetchone()
    return bool(row)


def mark_webhook_processed(conn: DbConn, event_id: str, event_type: str, detail: str = "") -> None:
    ensure_stripe_tables(conn)
    conn.execute(
        """INSERT INTO webclaw_stripe_webhook_event (id, event_type, processed_at, detail)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(id) DO NOTHING""",
        (event_id, event_type, _now(), detail[:2000]),
    )
    conn.commit()


def handle_platform_webhook_event(event: dict) -> dict:
    """Process a verified Stripe webhook event for SaaS subscriptions."""
    conn = db.get_connection()
    event_id = event.get("id") or str(uuid.uuid4())
    event_type = event.get("type") or ""
    if webhook_already_processed(conn, event_id):
        return {"status": "ok", "duplicate": True, "event_type": event_type}

    data_obj = (event.get("data") or {}).get("object") or {}
    stripe_client = None
    keys = platform_stripe_keys(conn)
    if keys.get("secret_key"):
        try:
            stripe_client = get_stripe_client(keys["secret_key"])
        except ValueError:
            pass

    handled = False

    if event_type == "checkout.session.completed":
        tenant_id = resolve_tenant_from_stripe_object(conn, data_obj)
        sub_id = data_obj.get("subscription")
        if tenant_id and sub_id and stripe_client:
            subscription = stripe_client.Subscription.retrieve(sub_id)
            apply_subscription_state(conn, tenant_id=tenant_id, subscription=subscription)
            handled = True
        elif tenant_id:
            plan_code = (data_obj.get("metadata") or {}).get("plan_code") or "starter"
            set_tenant_plan(tenant_id, plan_code)
            set_tenant_status(tenant_id, "active")
            handled = True

    elif event_type in (
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    ):
        tenant_id = resolve_tenant_from_stripe_object(conn, data_obj)
        if tenant_id:
            if event_type == "customer.subscription.deleted":
                data_obj = {**data_obj, "status": "canceled"}
            apply_subscription_state(conn, tenant_id=tenant_id, subscription=data_obj)
            handled = True

    elif event_type == "invoice.payment_failed":
        tenant_id = resolve_tenant_from_stripe_object(conn, data_obj)
        if not tenant_id:
            # invoice.customer → lookup
            tenant_id = resolve_tenant_from_stripe_object(
                conn, {"customer": data_obj.get("customer"), "metadata": data_obj.get("metadata") or {}},
            )
        if tenant_id:
            from admin_platform import _record_subscription_event

            row = get_stripe_customer_row(conn, tenant_id) or {}
            _upsert_stripe_customer_row(
                conn,
                tenant_id=tenant_id,
                stripe_customer_id=row.get("stripe_customer_id") or str(data_obj.get("customer") or ""),
                status="past_due",
            )
            _record_subscription_event(
                conn, tenant_id, "stripe_payment_failed",
                detail=json.dumps({"invoice": data_obj.get("id"), "attempt_count": data_obj.get("attempt_count")}),
                actor_email="stripe_webhook",
            )
            # Suspend after repeated failures
            attempts = int(data_obj.get("attempt_count") or 0)
            if attempts >= 3:
                set_tenant_status(tenant_id, "suspended")
                _record_subscription_event(
                    conn, tenant_id, "stripe_hotel_suspended",
                    detail="Payment failed after 3 attempts",
                    actor_email="stripe_webhook",
                )
            handled = True

    elif event_type in ("invoice.paid", "invoice.payment_succeeded"):
        tenant_id = resolve_tenant_from_stripe_object(conn, data_obj)
        if not tenant_id:
            tenant_id = resolve_tenant_from_stripe_object(
                conn, {"customer": data_obj.get("customer"), "metadata": data_obj.get("metadata") or {}},
            )
        sub_id = data_obj.get("subscription")
        if tenant_id and sub_id and stripe_client:
            subscription = stripe_client.Subscription.retrieve(sub_id)
            apply_subscription_state(conn, tenant_id=tenant_id, subscription=subscription)
            handled = True
        elif tenant_id:
            set_tenant_status(tenant_id, "active")
            handled = True

    mark_webhook_processed(conn, event_id, event_type, detail=f"handled={handled}")
    return {"status": "ok", "handled": handled, "event_type": event_type}


# ── Operator billing tools (invoices / refunds / credits / coupons) ───────────

def list_invoices_for_tenant(conn: DbConn, tenant_id: str, *, limit: int = 20) -> list[dict]:
    """List Stripe invoices for a hotel customer (empty if Stripe not linked)."""
    if not platform_stripe_configured(conn):
        raise ValueError("Stripe is not configured")
    row = get_stripe_customer_row(conn, tenant_id) or {}
    customer_id = row.get("stripe_customer_id")
    if not customer_id:
        return []
    stripe = get_stripe_client(platform_stripe_keys(conn)["secret_key"])
    invoices = stripe.Invoice.list(customer=customer_id, limit=max(1, min(limit, 100)))
    out = []
    for inv in invoices.auto_paging_iter() if False else (invoices.data or []):
        out.append(
            {
                "id": inv.get("id"),
                "number": inv.get("number"),
                "status": inv.get("status"),
                "amount_due": inv.get("amount_due"),
                "amount_paid": inv.get("amount_paid"),
                "currency": inv.get("currency"),
                "hosted_invoice_url": inv.get("hosted_invoice_url"),
                "invoice_pdf": inv.get("invoice_pdf"),
                "created": inv.get("created"),
                "period_start": inv.get("period_start"),
                "period_end": inv.get("period_end"),
            }
        )
        if len(out) >= limit:
            break
    return out


def create_refund(
    conn: DbConn,
    *,
    payment_intent_id: str | None = None,
    charge_id: str | None = None,
    amount_cents: int | None = None,
    reason: str | None = None,
) -> dict:
    if not platform_stripe_configured(conn):
        raise ValueError("Stripe is not configured")
    if not payment_intent_id and not charge_id:
        raise ValueError("payment_intent_id or charge_id is required")
    stripe = get_stripe_client(platform_stripe_keys(conn)["secret_key"])
    params: dict = {}
    if payment_intent_id:
        params["payment_intent"] = payment_intent_id
    if charge_id:
        params["charge"] = charge_id
    if amount_cents is not None:
        params["amount"] = int(amount_cents)
    if reason:
        params["reason"] = reason
    refund = stripe.Refund.create(**params)
    return {
        "id": refund.get("id"),
        "status": refund.get("status"),
        "amount": refund.get("amount"),
        "currency": refund.get("currency"),
    }


def create_customer_credit(
    conn: DbConn,
    tenant_id: str,
    *,
    amount_cents: int,
    currency: str = "usd",
    description: str = "Platform credit",
) -> dict:
    if not platform_stripe_configured(conn):
        raise ValueError("Stripe is not configured")
    if amount_cents <= 0:
        raise ValueError("amount_cents must be positive")
    row = get_stripe_customer_row(conn, tenant_id) or {}
    customer_id = row.get("stripe_customer_id")
    if not customer_id:
        raise ValueError("Hotel has no Stripe customer")
    stripe = get_stripe_client(platform_stripe_keys(conn)["secret_key"])
    # Negative amount = credit on customer balance
    txn = stripe.Customer.create_balance_transaction(
        customer_id,
        amount=-abs(int(amount_cents)),
        currency=(currency or "usd").lower(),
        description=description,
    )
    return {
        "id": txn.get("id"),
        "amount": txn.get("amount"),
        "currency": txn.get("currency"),
        "ending_balance": txn.get("ending_balance"),
    }


def create_coupon(
    conn: DbConn,
    *,
    code: str,
    percent_off: float | None = None,
    amount_off_cents: int | None = None,
    currency: str = "usd",
    duration: str = "once",
) -> dict:
    if not platform_stripe_configured(conn):
        raise ValueError("Stripe is not configured")
    if not percent_off and not amount_off_cents:
        raise ValueError("percent_off or amount_off_cents required")
    stripe = get_stripe_client(platform_stripe_keys(conn)["secret_key"])
    params: dict = {
        "name": code.strip().upper(),
        "duration": duration if duration in ("once", "repeating", "forever") else "once",
        "id": code.strip().upper()[:40],
    }
    if percent_off:
        params["percent_off"] = float(percent_off)
    else:
        params["amount_off"] = int(amount_off_cents or 0)
        params["currency"] = (currency or "usd").lower()
    coupon = stripe.Coupon.create(**params)
    promo = stripe.PromotionCode.create(coupon=coupon["id"], code=code.strip().upper())
    return {
        "coupon_id": coupon.get("id"),
        "promotion_code_id": promo.get("id"),
        "code": promo.get("code"),
        "percent_off": coupon.get("percent_off"),
        "amount_off": coupon.get("amount_off"),
    }


def apply_coupon_to_tenant(conn: DbConn, tenant_id: str, promotion_code: str) -> dict:
    if not platform_stripe_configured(conn):
        raise ValueError("Stripe is not configured")
    row = get_stripe_customer_row(conn, tenant_id) or {}
    customer_id = row.get("stripe_customer_id")
    sub_id = row.get("stripe_subscription_id")
    if not customer_id:
        raise ValueError("Hotel has no Stripe customer")
    if not sub_id:
        raise ValueError("Hotel has no active Stripe subscription")
    stripe = get_stripe_client(platform_stripe_keys(conn)["secret_key"])
    # Resolve promotion code id
    codes = stripe.PromotionCode.list(code=promotion_code.strip().upper(), active=True, limit=1)
    data = codes.data or []
    if not data:
        raise ValueError("Promotion code not found")
    promo_id = data[0]["id"]
    sub = stripe.Subscription.modify(sub_id, promotion_code=promo_id)
    return {"subscription_id": sub.get("id"), "status": sub.get("status"), "promotion_code": promotion_code}
