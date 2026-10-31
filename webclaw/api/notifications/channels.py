"""Delivery channels: SendGrid / SMTP email and Twilio SMS / WhatsApp."""
from __future__ import annotations

import base64
import logging
import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import httpx

log = logging.getLogger("webclaw.notifications.channels")


def load_messaging_credentials() -> dict[str, Any]:
    """Platform SendGrid / Twilio / SMTP credentials (env overrides settings)."""
    from admin_ops import _load_settings
    import db

    conn = db.get_connection()
    settings = _load_settings(conn)
    integ = settings.get("integrations") or {}
    sendgrid = dict(integ.get("sendgrid") or {})
    twilio = dict(integ.get("twilio") or {})
    smtp = dict(integ.get("smtp") or {})

    # Env overrides
    if os.environ.get("SENDGRID_API_KEY"):
        sendgrid["api_key"] = os.environ["SENDGRID_API_KEY"].strip()
    if os.environ.get("SENDGRID_FROM_EMAIL"):
        sendgrid["from_email"] = os.environ["SENDGRID_FROM_EMAIL"].strip()
    if os.environ.get("SENDGRID_FROM_NAME"):
        sendgrid["from_name"] = os.environ["SENDGRID_FROM_NAME"].strip()
    if os.environ.get("TWILIO_ACCOUNT_SID"):
        twilio["account_sid"] = os.environ["TWILIO_ACCOUNT_SID"].strip()
    if os.environ.get("TWILIO_AUTH_TOKEN"):
        twilio["auth_token"] = os.environ["TWILIO_AUTH_TOKEN"].strip()
    if os.environ.get("TWILIO_FROM_NUMBER"):
        twilio["from_number"] = os.environ["TWILIO_FROM_NUMBER"].strip()
    if os.environ.get("TWILIO_WHATSAPP_FROM"):
        twilio["whatsapp_from"] = os.environ["TWILIO_WHATSAPP_FROM"].strip()
    if os.environ.get("SMTP_HOST"):
        smtp["host"] = os.environ["SMTP_HOST"].strip()
    if os.environ.get("SMTP_PORT"):
        smtp["port"] = os.environ["SMTP_PORT"].strip()
    if os.environ.get("SMTP_USERNAME"):
        smtp["username"] = os.environ["SMTP_USERNAME"].strip()
    if os.environ.get("SMTP_PASSWORD"):
        smtp["password"] = os.environ["SMTP_PASSWORD"].strip()
    if os.environ.get("SMTP_FROM_EMAIL"):
        smtp["from_email"] = os.environ["SMTP_FROM_EMAIL"].strip()
    if os.environ.get("SMTP_USE_TLS"):
        smtp["use_tls"] = os.environ["SMTP_USE_TLS"].strip().lower() in ("1", "true", "yes")

    return {"sendgrid": sendgrid, "twilio": twilio, "smtp": smtp}


def email_configured(creds: dict | None = None) -> bool:
    c = creds or load_messaging_credentials()
    sg = c.get("sendgrid") or {}
    smtp = c.get("smtp") or {}
    if sg.get("api_key") and sg.get("from_email"):
        return True
    if smtp.get("host") and (smtp.get("from_email") or smtp.get("username")):
        return True
    return False


def sms_configured(creds: dict | None = None) -> bool:
    c = creds or load_messaging_credentials()
    tw = c.get("twilio") or {}
    return bool(tw.get("account_sid") and tw.get("auth_token") and tw.get("from_number"))


def whatsapp_configured(creds: dict | None = None) -> bool:
    c = creds or load_messaging_credentials()
    tw = c.get("twilio") or {}
    return bool(
        tw.get("account_sid")
        and tw.get("auth_token")
        and (tw.get("whatsapp_from") or tw.get("from_number"))
    )


async def send_email(
    *,
    to: str,
    subject: str,
    body: str,
    attachments: list[dict] | None = None,
    creds: dict | None = None,
    tenant_id: str | None = None,
) -> dict:
    """Send email via SendGrid (preferred) or SMTP fallback.

    attachments: list of {filename, content: bytes, mime_type}
    When tenant_id is set, white-label email_from_* overrides the platform From.
    """
    to = (to or "").strip()
    if not to or "@" not in to:
        return {"ok": False, "error": "Missing or invalid recipient email"}

    c = creds or load_messaging_credentials()
    from_email_override = None
    from_name_override = None
    if tenant_id:
        try:
            from admin_growth import get_tenant_branding

            brand = get_tenant_branding(tenant_id) or {}
            if brand.get("email_from_address"):
                from_email_override = str(brand["email_from_address"])
            if brand.get("email_from_name"):
                from_name_override = str(brand["email_from_name"])
        except Exception:
            pass

    sg = dict(c.get("sendgrid") or {})
    if from_email_override:
        sg["from_email"] = from_email_override
    if from_name_override:
        sg["from_name"] = from_name_override
    if sg.get("api_key") and sg.get("from_email"):
        return await _send_sendgrid(
            api_key=str(sg["api_key"]),
            from_email=str(sg["from_email"]),
            from_name=str(sg.get("from_name") or "HotelPilot"),
            to=to,
            subject=subject,
            body=body,
            attachments=attachments,
        )

    smtp = dict(c.get("smtp") or {})
    if from_email_override:
        smtp["from_email"] = from_email_override
    if from_name_override:
        smtp["from_name"] = from_name_override
    if smtp.get("host"):
        return await _send_smtp(
            smtp=smtp,
            to=to,
            subject=subject,
            body=body,
            attachments=attachments,
        )

    return {"ok": False, "error": "Email is not configured (SendGrid or SMTP)"}


async def _send_sendgrid(
    *,
    api_key: str,
    from_email: str,
    from_name: str,
    to: str,
    subject: str,
    body: str,
    attachments: list[dict] | None,
) -> dict:
    payload: dict[str, Any] = {
        "personalizations": [{"to": [{"email": to}]}],
        "from": {"email": from_email, "name": from_name},
        "subject": subject or "(no subject)",
        "content": [
            {"type": "text/plain", "value": body or ""},
            {
                "type": "text/html",
                "value": "<pre style='font-family:inherit;white-space:pre-wrap'>"
                + _html_escape(body or "")
                + "</pre>",
            },
        ],
    }
    if attachments:
        payload["attachments"] = []
        for att in attachments:
            content = att.get("content") or b""
            if isinstance(content, str):
                content = content.encode("utf-8")
            payload["attachments"].append(
                {
                    "content": base64.b64encode(content).decode("ascii"),
                    "type": att.get("mime_type") or "application/pdf",
                    "filename": att.get("filename") or "attachment.pdf",
                    "disposition": "attachment",
                }
            )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if res.status_code in (200, 202):
            return {"ok": True, "provider": "sendgrid", "status_code": res.status_code}
        return {
            "ok": False,
            "provider": "sendgrid",
            "error": f"SendGrid HTTP {res.status_code}: {res.text[:300]}",
        }
    except Exception as exc:
        log.exception("SendGrid send failed")
        return {"ok": False, "provider": "sendgrid", "error": str(exc)}


async def _send_smtp(
    *,
    smtp: dict,
    to: str,
    subject: str,
    body: str,
    attachments: list[dict] | None,
) -> dict:
    host = str(smtp.get("host") or "")
    port = int(smtp.get("port") or 587)
    username = str(smtp.get("username") or "")
    password = str(smtp.get("password") or "")
    from_email = str(smtp.get("from_email") or username or "")
    from_name = str(smtp.get("from_name") or "HotelPilot")
    use_tls = smtp.get("use_tls", True)
    if isinstance(use_tls, str):
        use_tls = use_tls.lower() in ("1", "true", "yes")

    if not host or not from_email:
        return {"ok": False, "provider": "smtp", "error": "SMTP host/from incomplete"}

    msg = MIMEMultipart()
    msg["From"] = f"{from_name} <{from_email}>" if from_name else from_email
    msg["To"] = to
    msg["Subject"] = subject or "(no subject)"
    msg.attach(MIMEText(body or "", "plain", "utf-8"))
    for att in attachments or []:
        content = att.get("content") or b""
        if isinstance(content, str):
            content = content.encode("utf-8")
        part = MIMEApplication(content, Name=att.get("filename") or "attachment.pdf")
        part["Content-Disposition"] = (
            f'attachment; filename="{att.get("filename") or "attachment.pdf"}"'
        )
        msg.attach(part)

    try:
        # smtplib is blocking — run in thread via asyncio if needed; keep sync for simplicity
        import asyncio

        def _send():
            if use_tls:
                server = smtplib.SMTP(host, port, timeout=30)
                server.ehlo()
                server.starttls()
                server.ehlo()
            else:
                server = smtplib.SMTP(host, port, timeout=30)
            try:
                if username:
                    server.login(username, password)
                server.sendmail(from_email, [to], msg.as_string())
            finally:
                try:
                    server.quit()
                except Exception:
                    pass

        await asyncio.to_thread(_send)
        return {"ok": True, "provider": "smtp"}
    except Exception as exc:
        log.exception("SMTP send failed")
        return {"ok": False, "provider": "smtp", "error": str(exc)}


async def send_sms(*, to: str, body: str, creds: dict | None = None) -> dict:
    """Send SMS via Twilio Messages API."""
    to = _normalize_phone(to)
    if not to:
        return {"ok": False, "error": "Missing or invalid phone number"}
    c = creds or load_messaging_credentials()
    tw = c.get("twilio") or {}
    sid = str(tw.get("account_sid") or "")
    token = str(tw.get("auth_token") or "")
    from_number = str(tw.get("from_number") or "")
    if not (sid and token and from_number):
        return {"ok": False, "error": "Twilio SMS is not configured"}
    return await _twilio_message(
        account_sid=sid,
        auth_token=token,
        from_number=from_number,
        to=to,
        body=body,
        channel="sms",
    )


async def send_whatsapp(*, to: str, body: str, creds: dict | None = None) -> dict:
    """Send WhatsApp via Twilio (whatsapp:+E164)."""
    to = _normalize_phone(to)
    if not to:
        return {"ok": False, "error": "Missing or invalid phone number"}
    c = creds or load_messaging_credentials()
    tw = c.get("twilio") or {}
    sid = str(tw.get("account_sid") or "")
    token = str(tw.get("auth_token") or "")
    wa_from = str(tw.get("whatsapp_from") or tw.get("from_number") or "")
    if not (sid and token and wa_from):
        return {"ok": False, "error": "Twilio WhatsApp is not configured"}
    if not wa_from.startswith("whatsapp:"):
        wa_from = f"whatsapp:{wa_from}"
    wa_to = to if to.startswith("whatsapp:") else f"whatsapp:{to}"
    return await _twilio_message(
        account_sid=sid,
        auth_token=token,
        from_number=wa_from,
        to=wa_to,
        body=body,
        channel="whatsapp",
    )


async def _twilio_message(
    *,
    account_sid: str,
    auth_token: str,
    from_number: str,
    to: str,
    body: str,
    channel: str,
) -> dict:
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.post(
                url,
                data={"From": from_number, "To": to, "Body": body or ""},
                auth=(account_sid, auth_token),
            )
        if res.status_code in (200, 201):
            data = res.json()
            return {
                "ok": True,
                "provider": "twilio",
                "channel": channel,
                "sid": data.get("sid"),
            }
        return {
            "ok": False,
            "provider": "twilio",
            "channel": channel,
            "error": f"Twilio HTTP {res.status_code}: {res.text[:300]}",
        }
    except Exception as exc:
        log.exception("Twilio %s failed", channel)
        return {"ok": False, "provider": "twilio", "channel": channel, "error": str(exc)}


def _normalize_phone(raw: str | None) -> str:
    if not raw:
        return ""
    s = str(raw).strip().replace(" ", "").replace("-", "")
    if s.startswith("whatsapp:"):
        return s
    if s.startswith("+"):
        return s
    digits = "".join(c for c in s if c.isdigit())
    if len(digits) >= 10:
        return f"+{digits}"
    return s


def _html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
