"""Hotel notification settings + manual dispatch / pre-arrival runner."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from auth.deps import get_current_user, require_hotel_tenant
from notifications import channels
from notifications.defaults import TRIGGER_META, TRIGGERS, merge_notification_settings
from notifications.engine import dispatch_trigger
from notifications.scheduler import run_pre_arrival_once
from notifications.store import list_recent_logs, load_hotel_notification_settings, save_hotel_notification_settings
from rbac import require_permission
from tenant import TenantContext

router = APIRouter(prefix="/api/v1/hotel/notifications", tags=["notifications"])


class TriggerChannels(BaseModel):
    email: bool = True
    sms: bool = False
    whatsapp: bool = False


class TriggerConfig(BaseModel):
    enabled: bool = True
    channels: TriggerChannels = Field(default_factory=TriggerChannels)
    email_subject: str = ""
    email_body: str = ""
    sms_body: str = ""
    whatsapp_body: str = ""


class NotificationSettingsUpdate(BaseModel):
    booking_confirmation: TriggerConfig | None = None
    pre_arrival: TriggerConfig | None = None
    folio_receipt: TriggerConfig | None = None


class ManualDispatchRequest(BaseModel):
    trigger: str = Field(min_length=3, max_length=40)
    reservation_id: str | None = None
    folio_id: str | None = None
    force: bool = False


@router.get("/settings")
@require_permission("hotel.manage")
async def get_notification_settings(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
):
    settings = load_hotel_notification_settings(tenant_id)
    creds_ok = {
        "email": channels.email_configured(),
        "sms": channels.sms_configured(),
        "whatsapp": channels.whatsapp_configured(),
    }
    return {
        "status": "ok",
        "settings": settings,
        "triggers": [
            {
                "key": key,
                **TRIGGER_META[key],
                "config": settings[key],
            }
            for key in TRIGGERS
        ],
        "providers": creds_ok,
    }


@router.put("/settings")
@require_permission("hotel.manage")
async def update_notification_settings(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
):
    try:
        body = await request.json()
        req = NotificationSettingsUpdate(**(body or {}))
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)

    patch = req.model_dump(exclude_none=True)
    # Convert nested channels models already dumped as dict
    try:
        saved = save_hotel_notification_settings(tenant_id, patch)
    except ValueError as e:
        return JSONResponse({"status": "error", "message": str(e)}, 404)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 500)

    return {"status": "ok", "settings": saved, "message": "Notification settings saved"}


@router.get("/logs")
@require_permission("hotel.manage")
async def get_notification_logs(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
    limit: int = 50,
):
    return {"status": "ok", "logs": list_recent_logs(tenant_id, limit=limit)}


@router.post("/dispatch")
@require_permission("hotel.manage")
async def manual_dispatch(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
):
    try:
        body = await request.json()
        req = ManualDispatchRequest(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)

    if req.trigger not in TRIGGERS:
        return JSONResponse({"status": "error", "message": "Unknown trigger"}, 400)
    if not req.reservation_id and not req.folio_id:
        return JSONResponse(
            {"status": "error", "message": "reservation_id or folio_id required"},
            400,
        )

    result = await dispatch_trigger(
        tenant_id=tenant_id,
        trigger_key=req.trigger,
        reservation_id=req.reservation_id,
        folio_id=req.folio_id,
        force=req.force,
    )
    status = 200 if result.get("status") == "ok" else 400
    return JSONResponse(result, status_code=status)


@router.post("/run-pre-arrival")
@require_permission("hotel.manage")
async def run_pre_arrival(
    request: Request,
    ctx: TenantContext = Depends(get_current_user),
):
    """Run the 24h pre-arrival job (hotel staff or SuperAdmin)."""
    # SuperAdmin may run globally; hotel users only process their tenant via engine filters
    summary = await run_pre_arrival_once()
    if not ctx.is_superadmin and ctx.isolation_id:
        tid = ctx.isolation_id
        summary["results"] = [
            r for r in summary.get("results") or [] if r.get("tenant_id") == tid
        ]
        summary["candidates"] = len(summary["results"])
    return summary
