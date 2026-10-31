"""Default guest notification triggers and message templates."""
from __future__ import annotations

TRIGGERS = ("booking_confirmation", "pre_arrival", "folio_receipt")

TRIGGER_META = {
    "booking_confirmation": {
        "label": "Booking confirmation",
        "description": "Sent when a new reservation is created.",
        "placeholders": [
            "guest_name", "guest_first_name", "hotel_name", "confirmation_code",
            "checkin_date", "checkout_date", "room_type", "room_number",
            "nights", "rate", "total_amount", "hotel_phone", "hotel_email",
            "check_in_time", "check_out_time", "portal_url",
        ],
    },
    "pre_arrival": {
        "label": "Pre-arrival instructions",
        "description": "Sent ~24 hours before check-in with arrival details.",
        "placeholders": [
            "guest_name", "guest_first_name", "hotel_name", "confirmation_code",
            "checkin_date", "checkout_date", "room_type", "room_number",
            "check_in_time", "check_out_time", "hotel_address", "hotel_phone",
            "cancellation_policy", "nights", "portal_url",
        ],
    },
    "folio_receipt": {
        "label": "Folio receipt",
        "description": "Itemized PDF receipt emailed after settlement or checkout.",
        "placeholders": [
            "guest_name", "hotel_name", "confirmation_code", "checkin_date",
            "checkout_date", "total_amount", "balance", "folio_id",
            "payment_method", "charges_summary", "currency",
        ],
    },
}

DEFAULT_TEMPLATES = {
    "booking_confirmation": {
        "enabled": True,
        "channels": {"email": True, "sms": True, "whatsapp": False},
        "email_subject": "Booking confirmed at {{hotel_name}}",
        "email_body": (
            "Dear {{guest_name}},\n\n"
            "Your reservation at {{hotel_name}} is confirmed.\n\n"
            "Confirmation: {{confirmation_code}}\n"
            "Check-in: {{checkin_date}} (from {{check_in_time}})\n"
            "Check-out: {{checkout_date}} (by {{check_out_time}})\n"
            "Room type: {{room_type}}\n"
            "Nights: {{nights}}\n"
            "Rate: {{rate}} {{currency}}\n\n"
            "Complete pre-arrival registration and pay your folio here:\n"
            "{{portal_url}}\n\n"
            "We look forward to welcoming you.\n"
            "{{hotel_name}}\n{{hotel_phone}}"
        ),
        "sms_body": (
            "{{hotel_name}}: Booking confirmed for {{guest_first_name}}. "
            "Check-in {{checkin_date}}. Conf #{{confirmation_code}}. "
            "Guest portal: {{portal_url}}"
        ),
        "whatsapp_body": (
            "Hi {{guest_first_name}}! Your stay at {{hotel_name}} is confirmed "
            "({{checkin_date}} → {{checkout_date}}). Confirmation {{confirmation_code}}."
        ),
    },
    "pre_arrival": {
        "enabled": True,
        "channels": {"email": True, "sms": True, "whatsapp": False},
        "email_subject": "See you tomorrow at {{hotel_name}}",
        "email_body": (
            "Dear {{guest_name}},\n\n"
            "Your check-in at {{hotel_name}} is tomorrow ({{checkin_date}}).\n\n"
            "Arrival from: {{check_in_time}}\n"
            "Address: {{hotel_address}}\n"
            "Room type: {{room_type}}\n"
            "Confirmation: {{confirmation_code}}\n\n"
            "Finish registration or review your stay:\n"
            "{{portal_url}}\n\n"
            "Cancellation policy:\n{{cancellation_policy}}\n\n"
            "Questions? Call {{hotel_phone}} or email {{hotel_email}}.\n\n"
            "Safe travels,\n{{hotel_name}}"
        ),
        "sms_body": (
            "{{hotel_name}}: Check-in tomorrow {{checkin_date}} from {{check_in_time}}. "
            "Conf #{{confirmation_code}}. Portal: {{portal_url}}"
        ),
        "whatsapp_body": (
            "Hi {{guest_first_name}} — looking forward to seeing you tomorrow at "
            "{{hotel_name}} (check-in from {{check_in_time}}). Conf {{confirmation_code}}."
        ),
    },
    "folio_receipt": {
        "enabled": True,
        "channels": {"email": True, "sms": False, "whatsapp": False},
        "email_subject": "Your receipt from {{hotel_name}}",
        "email_body": (
            "Dear {{guest_name}},\n\n"
            "Thank you for staying at {{hotel_name}}.\n\n"
            "Folio: {{folio_id}}\n"
            "Stay: {{checkin_date}} → {{checkout_date}}\n"
            "Total charged: {{total_amount}} {{currency}}\n"
            "Balance: {{balance}} {{currency}}\n\n"
            "Charges:\n{{charges_summary}}\n\n"
            "An itemized PDF receipt is attached.\n\n"
            "We hope to see you again.\n{{hotel_name}}"
        ),
        "sms_body": (
            "{{hotel_name}}: Receipt for {{total_amount}} {{currency}} "
            "(folio {{folio_id}}). Thank you for your stay!"
        ),
        "whatsapp_body": (
            "Hi {{guest_first_name}}, your {{hotel_name}} folio receipt for "
            "{{total_amount}} {{currency}} is ready. Thank you for staying with us!"
        ),
    },
}


def deep_merge_trigger(defaults: dict, override: dict | None) -> dict:
    """Merge a hotel override onto a default trigger config."""
    out = {
        "enabled": defaults.get("enabled", True),
        "channels": dict(defaults.get("channels") or {}),
        "email_subject": defaults.get("email_subject") or "",
        "email_body": defaults.get("email_body") or "",
        "sms_body": defaults.get("sms_body") or "",
        "whatsapp_body": defaults.get("whatsapp_body") or "",
    }
    if not isinstance(override, dict):
        return out
    if "enabled" in override:
        out["enabled"] = bool(override["enabled"])
    ch = override.get("channels")
    if isinstance(ch, dict):
        for k in ("email", "sms", "whatsapp"):
            if k in ch:
                out["channels"][k] = bool(ch[k])
    for key in ("email_subject", "email_body", "sms_body", "whatsapp_body"):
        if key in override and override[key] is not None:
            out[key] = str(override[key])
    return out


def merge_notification_settings(raw: dict | None) -> dict:
    """Return full trigger config with defaults applied."""
    raw = raw if isinstance(raw, dict) else {}
    merged = {}
    for trigger in TRIGGERS:
        merged[trigger] = deep_merge_trigger(
            DEFAULT_TEMPLATES[trigger],
            raw.get(trigger) if isinstance(raw.get(trigger), dict) else None,
        )
    return merged
