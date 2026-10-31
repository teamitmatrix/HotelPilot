"""Guest notification dispatch engine."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import db
from notifications import channels
from notifications.context import load_folio_charge_rows, load_reservation_context
from notifications.defaults import TRIGGERS
from notifications.pdf_receipt import build_folio_pdf
from notifications.store import (
    already_sent,
    ensure_notification_tables,
    load_hotel_notification_settings,
    log_notification,
)
from notifications.templates import render_template, sanitize_context

log = logging.getLogger("webclaw.notifications")

# Map skill actions → notification trigger
ACTION_TRIGGERS: dict[str, str] = {
    "add-reservation": "booking_confirmation",
    "hospitality-add-reservation": "booking_confirmation",
    "settle-folio": "folio_receipt",
    "hospitality-settle-folio": "folio_receipt",
    "check-out": "folio_receipt",
    "hospitality-check-out": "folio_receipt",
}


async def dispatch_for_skill_action(
    *,
    skill: str,
    action: str,
    tenant_id: str | None,
    result: dict,
    params: dict | None = None,
) -> dict | None:
    """Fire-and-forget entry from the skill router after a successful mutation."""
    if skill not in ("hospitalityclaw", "hospitality"):
        return None
    if not tenant_id:
        return None
    trigger = ACTION_TRIGGERS.get(action)
    if not trigger:
        return None

    params = params or {}
    reservation_id = (
        result.get("reservation_id")
        or params.get("reservation_id")
        or params.get("id")
    )
    folio_id = result.get("folio_id") or params.get("folio_id")
    extra = {}
    if trigger == "folio_receipt":
        extra["payment_method"] = result.get("payment_method") or params.get("payment_method") or ""

    try:
        return await dispatch_trigger(
            tenant_id=str(tenant_id),
            trigger_key=trigger,
            reservation_id=str(reservation_id) if reservation_id else None,
            folio_id=str(folio_id) if folio_id else None,
            extra_context=extra,
        )
    except Exception:
        log.exception(
            "Notification dispatch failed trigger=%s tenant=%s",
            trigger,
            tenant_id,
        )
        return {"status": "error", "trigger": trigger}


async def dispatch_trigger(
    *,
    tenant_id: str,
    trigger_key: str,
    reservation_id: str | None = None,
    folio_id: str | None = None,
    extra_context: dict | None = None,
    force: bool = False,
) -> dict:
    """Send all enabled channels for a trigger."""
    if trigger_key not in TRIGGERS:
        return {"status": "error", "message": f"Unknown trigger: {trigger_key}"}

    settings = load_hotel_notification_settings(tenant_id)
    cfg = settings.get(trigger_key) or {}
    if not cfg.get("enabled") and not force:
        return {"status": "ok", "skipped": True, "reason": "trigger_disabled"}

    ctx_raw = load_reservation_context(
        tenant_id, reservation_id=reservation_id, folio_id=folio_id,
    )
    if not ctx_raw:
        return {"status": "error", "message": "Reservation/folio context not found"}

    if extra_context:
        ctx_raw.update({k: v for k, v in extra_context.items() if v is not None})
    ctx = sanitize_context(ctx_raw)

    reservation_id = ctx.get("reservation_id") or reservation_id
    folio_id = ctx.get("folio_id") or folio_id

    creds = channels.load_messaging_credentials()
    deliveries: list[dict] = []
    ch = cfg.get("channels") or {}

    # Email
    if ch.get("email"):
        deliveries.append(
            await _send_channel(
                tenant_id=tenant_id,
                trigger_key=trigger_key,
                channel="email",
                cfg=cfg,
                ctx=ctx,
                ctx_raw=ctx_raw,
                reservation_id=reservation_id,
                folio_id=folio_id,
                creds=creds,
                force=force,
            )
        )

    # SMS
    if ch.get("sms"):
        deliveries.append(
            await _send_channel(
                tenant_id=tenant_id,
                trigger_key=trigger_key,
                channel="sms",
                cfg=cfg,
                ctx=ctx,
                ctx_raw=ctx_raw,
                reservation_id=reservation_id,
                folio_id=folio_id,
                creds=creds,
                force=force,
            )
        )

    # WhatsApp
    if ch.get("whatsapp"):
        deliveries.append(
            await _send_channel(
                tenant_id=tenant_id,
                trigger_key=trigger_key,
                channel="whatsapp",
                cfg=cfg,
                ctx=ctx,
                ctx_raw=ctx_raw,
                reservation_id=reservation_id,
                folio_id=folio_id,
                creds=creds,
                force=force,
            )
        )

    return {
        "status": "ok",
        "trigger": trigger_key,
        "reservation_id": reservation_id,
        "folio_id": folio_id,
        "deliveries": deliveries,
    }


async def _send_channel(
    *,
    tenant_id: str,
    trigger_key: str,
    channel: str,
    cfg: dict,
    ctx: dict,
    ctx_raw: dict,
    reservation_id: str | None,
    folio_id: str | None,
    creds: dict,
    force: bool,
) -> dict:
    conn = ensure_notification_tables()
    if not force and already_sent(
        conn,
        tenant_id=tenant_id,
        trigger_key=trigger_key,
        channel=channel,
        reservation_id=reservation_id,
        folio_id=folio_id if trigger_key == "folio_receipt" else None,
    ):
        return {"channel": channel, "status": "skipped", "reason": "already_sent"}

    if channel == "email":
        to = ctx.get("guest_email") or ""
        if not to:
            log_notification(
                conn, tenant_id=tenant_id, trigger_key=trigger_key, channel=channel,
                status="skipped", reservation_id=reservation_id, folio_id=folio_id,
                detail="No guest email",
            )
            return {"channel": channel, "status": "skipped", "reason": "no_email"}

        subject = render_template(cfg.get("email_subject") or "", ctx)
        body = render_template(cfg.get("email_body") or "", ctx)
        attachments = None
        if trigger_key == "folio_receipt" and folio_id:
            charges = load_folio_charge_rows(tenant_id, folio_id)
            pdf = build_folio_pdf(
                hotel_name=ctx.get("hotel_name") or "Hotel",
                guest_name=ctx.get("guest_name") or "Guest",
                folio_id=folio_id,
                confirmation_code=ctx.get("confirmation_code") or "",
                checkin_date=ctx.get("checkin_date") or "",
                checkout_date=ctx.get("checkout_date") or "",
                currency=ctx.get("currency") or "USD",
                charges=charges,
                total_amount=ctx.get("total_amount") or "0",
                balance=ctx.get("balance") or "0",
            )
            attachments = [
                {
                    "filename": f"folio-{folio_id[:8]}.pdf",
                    "content": pdf,
                    "mime_type": "application/pdf",
                }
            ]
        result = await channels.send_email(
            to=to, subject=subject, body=body, attachments=attachments, creds=creds,
        )
        status = "sent" if result.get("ok") else "failed"
        log_notification(
            conn, tenant_id=tenant_id, trigger_key=trigger_key, channel=channel,
            status=status, reservation_id=reservation_id, folio_id=folio_id,
            recipient=to, detail=result.get("error") or result.get("provider") or "",
        )
        return {"channel": channel, "status": status, "recipient": to, **result}

    # SMS / WhatsApp
    to = ctx.get("guest_phone") or ""
    if not to:
        log_notification(
            conn, tenant_id=tenant_id, trigger_key=trigger_key, channel=channel,
            status="skipped", reservation_id=reservation_id, folio_id=folio_id,
            detail="No guest phone",
        )
        return {"channel": channel, "status": "skipped", "reason": "no_phone"}

    body_key = "sms_body" if channel == "sms" else "whatsapp_body"
    body = render_template(cfg.get(body_key) or cfg.get("sms_body") or "", ctx)
    if channel == "sms":
        result = await channels.send_sms(to=to, body=body, creds=creds)
    else:
        result = await channels.send_whatsapp(to=to, body=body, creds=creds)
    status = "sent" if result.get("ok") else "failed"
    log_notification(
        conn, tenant_id=tenant_id, trigger_key=trigger_key, channel=channel,
        status=status, reservation_id=reservation_id, folio_id=folio_id,
        recipient=to, detail=result.get("error") or result.get("sid") or "",
    )
    return {"channel": channel, "status": status, "recipient": to, **result}


def schedule_dispatch(**kwargs) -> None:
    """Non-blocking dispatch from sync contexts."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(dispatch_for_skill_action(**kwargs))
    except RuntimeError:
        # No running loop — run synchronously in a new loop (tests / scripts)
        asyncio.run(dispatch_for_skill_action(**kwargs))
