"""Tests for Stripe SaaS billing helpers and webhook handling."""
from __future__ import annotations

import json
import os
import sys
import uuid
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pgutil import connect_web, connect_erp

import stripe_billing as sb
from admin_platform import ensure_billing_tables, ensure_subscription_plans


def _web(setup_dbs):
    c = connect_web()
    return c


def _seed_tenant(*, name="Stripe Hotel") -> str:
    tid = str(uuid.uuid4())
    erp = connect_erp()
    abbr = tid.replace("-", "")[:8]
    erp.execute(
        "INSERT INTO company (id, name, abbr) VALUES (?, ?, ?)",
        (tid, f"{name}-{abbr}", abbr),
    )
    erp.execute(
        """INSERT INTO pms_tenant
           (id, company_id, hotel_name, admin_email, timezone, country, currency,
            subscription_tier, status)
           VALUES (?, ?, ?, 'owner@stripe.test', 'UTC', 'US', 'USD',
                   'starter', 'active')""",
        (tid, tid, name),
    )
    erp.commit()
    erp.close()
    return tid


def test_amount_to_cents():
    assert sb.amount_to_cents("49.00", "USD") == 4900
    assert sb.amount_to_cents("10.5", "USD") == 1050
    assert sb.amount_to_cents("1000", "JPY") == 1000


def test_ensure_stripe_tables(setup_dbs):
    conn = _web(setup_dbs)
    sb.ensure_stripe_tables(conn)
    cols = {
        r[0]
        for r in conn.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'webclaw'
              AND table_name = 'webclaw_subscription_plan'
            """
        ).fetchall()
    }
    assert "stripe_price_monthly_id" in cols
    row = conn.execute(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'webclaw'
          AND table_name = 'webclaw_stripe_customer'
        """
    ).fetchone()
    conn.close()
    assert row is not None


def test_platform_keys_from_settings(setup_dbs, monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_PUBLISHABLE_KEY", raising=False)
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    conn = _web(setup_dbs)
    conn.execute(
        "INSERT INTO webclaw_platform_settings (id, data) VALUES ('default', ?)",
        (
            json.dumps(
                {
                    "integrations": {
                        "stripe": {
                            "secret_key": "sk_test_abc",
                            "publishable_key": "pk_test_abc",
                            "webhook_secret": "whsec_abc",
                        }
                    }
                }
            ),
        ),
    )
    conn.commit()
    keys = sb.platform_stripe_keys(conn)
    conn.close()
    assert keys["secret_key"] == "sk_test_abc"
    assert keys["publishable_key"] == "pk_test_abc"
    assert sb.platform_stripe_configured()


def test_webhook_activates_subscription(setup_dbs, monkeypatch):
    tid = _seed_tenant()
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    conn = _web(setup_dbs)
    sb._upsert_stripe_customer_row(
        conn, tenant_id=tid, stripe_customer_id="cus_123", status="incomplete",
    )
    conn.close()

    event = {
        "id": "evt_test_1",
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "id": "sub_123",
                "object": "subscription",
                "customer": "cus_123",
                "status": "active",
                "current_period_end": 1893456000,
                "cancel_at_period_end": False,
                "metadata": {"tenant_id": tid, "plan_code": "business"},
                "items": {
                    "data": [
                        {"price": {"id": "price_biz", "metadata": {"plan_code": "business"}}}
                    ]
                },
            }
        },
    }

    result = sb.handle_platform_webhook_event(event)
    assert result["status"] == "ok"
    assert result["handled"] is True

    conn = _web(setup_dbs)
    row = sb.get_stripe_customer_row(conn, tid)
    conn.close()
    assert row["status"] == "active"
    assert row["plan_code"] == "business"
    assert row["stripe_subscription_id"] == "sub_123"

    import db

    erp = db.get_skill_db("erpclaw")
    tenant = erp.execute(
        "SELECT subscription_tier, status FROM pms_tenant WHERE id = ?", (tid,)
    ).fetchone()
    erp.close()
    assert tenant[0] == "professional"
    assert tenant[1] == "active"


def test_webhook_suspends_on_cancel(setup_dbs, monkeypatch):
    tid = _seed_tenant(name="Cancel Hotel")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    conn = _web(setup_dbs)
    sb._upsert_stripe_customer_row(
        conn, tenant_id=tid, stripe_customer_id="cus_9", status="active",
    )
    conn.close()
    event = {
        "id": "evt_cancel_1",
        "type": "customer.subscription.deleted",
        "data": {
            "object": {
                "id": "sub_9",
                "object": "subscription",
                "customer": "cus_9",
                "status": "canceled",
                "metadata": {"tenant_id": tid},
                "items": {"data": []},
            }
        },
    }
    sb.handle_platform_webhook_event(event)
    import db

    erp = db.get_skill_db("erpclaw")
    status = erp.execute("SELECT status FROM pms_tenant WHERE id = ?", (tid,)).fetchone()[0]
    erp.close()
    assert status == "suspended"


def test_webhook_idempotent(setup_dbs, monkeypatch):
    tid = _seed_tenant(name="Idempotent Hotel")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    conn = _web(setup_dbs)
    sb._upsert_stripe_customer_row(
        conn, tenant_id=tid, stripe_customer_id="cus_x", status="past_due",
    )
    conn.close()
    event = {
        "id": "evt_dup",
        "type": "invoice.paid",
        "data": {"object": {"customer": "cus_x", "metadata": {"tenant_id": tid}}},
    }
    first = sb.handle_platform_webhook_event(event)
    second = sb.handle_platform_webhook_event(event)
    assert first.get("handled") is True
    assert second.get("duplicate") is True


def test_payment_failed_suspends_after_retries(setup_dbs, monkeypatch):
    tid = _seed_tenant(name="Fail Hotel")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    conn = _web(setup_dbs)
    sb._upsert_stripe_customer_row(
        conn, tenant_id=tid, stripe_customer_id="cus_fail", status="active",
    )
    conn.close()
    event = {
        "id": "evt_fail_3",
        "type": "invoice.payment_failed",
        "data": {
            "object": {
                "id": "in_1",
                "customer": "cus_fail",
                "attempt_count": 3,
                "metadata": {"tenant_id": tid},
            }
        },
    }
    sb.handle_platform_webhook_event(event)
    import db

    erp = db.get_skill_db("erpclaw")
    status = erp.execute("SELECT status FROM pms_tenant WHERE id = ?", (tid,)).fetchone()[0]
    erp.close()
    assert status == "suspended"


@patch("stripe_billing.get_stripe_client")
def test_create_checkout_session_mocked(mock_client, setup_dbs, monkeypatch):
    tid = _seed_tenant(name="Checkout Hotel")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")

    stripe = MagicMock()
    stripe.Customer.create.return_value = {"id": "cus_new"}
    stripe.Product.create.return_value = {"id": "prod_1"}
    stripe.Price.create.return_value = {"id": "price_1"}
    stripe.checkout.Session.create.return_value = {
        "id": "cs_1",
        "url": "https://checkout.stripe.com/test",
    }
    mock_client.return_value = stripe

    result = sb.create_subscription_checkout(
        tenant_id=tid,
        email="owner@harbor.test",
        hotel_name="Harbor Inn",
        plan_code="starter",
        billing_cycle="monthly",
        success_url="http://localhost/ok",
        cancel_url="http://localhost/cancel",
    )
    assert result["checkout_url"].startswith("https://checkout.stripe.com")
    assert result["session_id"] == "cs_1"
    conn = _web(setup_dbs)
    row = sb.get_stripe_customer_row(conn, tid)
    conn.close()
    assert row["stripe_customer_id"] == "cus_new"
