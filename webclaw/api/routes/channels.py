"""Channel manager dashboard and OTA webhook."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from auth.deps import require_hotel_tenant
from rbac import require_any_permission, require_permission
from services import channel_manager as cm

router = APIRouter(tags=["channels"])


def _error(exc: Exception, status: int = 400):
    return JSONResponse({"status": "error", "message": str(exc)}, status_code=status)


@router.get("/api/v1/hotel/channels")
@require_permission("hotel.manage")
async def channel_dashboard(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
):
    try:
        data = cm.dashboard(tenant_id)
    except Exception as exc:
        return _error(exc, 500)
    return {"status": "ok", **data}


@router.get("/api/v1/hotel/channels/sync-log")
@require_any_permission("reports.generate", "hotel.manage")
async def channel_sync_log(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
    direction: str | None = Query(default=None),
    status: str | None = Query(default=None),
    ota: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    include_audit: bool = Query(default=True),
):
    """Ops sync events + optional booking-import audit trail (P0 report 9)."""
    try:
        if include_audit:
            data = cm.channel_sync_report(
                tenant_id,
                from_date=from_date,
                to_date=to_date,
                direction=direction,
                status=status,
                ota=ota,
                kind=kind,
                limit=limit,
            )
            # Keep flat logs for older clients; nest ops/audit for the report UI.
            ops = data.get("ops") or {}
            return {
                "status": "ok",
                **data,
                "logs": ops.get("logs") or [],
                "total": ops.get("total") or 0,
                "imports": (data.get("audit") or {}).get("imports") or [],
            }
        data = cm.list_sync_log(
            tenant_id,
            from_date=from_date,
            to_date=to_date,
            direction=direction,
            status=status,
            ota=ota,
            kind=kind,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        return _error(exc, 500)
    return {"status": "ok", **data}


@router.get("/api/v1/hotel/channels/booking-imports")
@require_any_permission("reports.generate", "hotel.manage")
async def channel_booking_imports(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
    ota: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    """Audit-only: OTA external booking → PMS reservation mapping."""
    try:
        data = cm.list_booking_imports(
            tenant_id,
            from_date=from_date,
            to_date=to_date,
            ota=ota,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        return _error(exc, 500)
    return {"status": "ok", **data}


@router.put("/api/v1/hotel/channels/connections")
@require_permission("hotel.manage")
async def upsert_connection(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
):
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        saved = cm.save_connection(tenant_id, body or {})
    except ValueError as exc:
        return _error(exc, 400)
    except Exception as exc:
        return _error(exc, 500)
    return {"status": "ok", "connection": saved}


@router.put("/api/v1/hotel/channels/mappings")
@require_permission("hotel.manage")
async def upsert_mapping(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
):
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        saved = cm.save_mapping(tenant_id, body or {})
    except ValueError as exc:
        return _error(exc, 400)
    except Exception as exc:
        return _error(exc, 500)
    return {"status": "ok", "mapping": saved}


@router.delete("/api/v1/hotel/channels/mappings/{mapping_id}")
@require_permission("hotel.manage")
async def remove_mapping(
    mapping_id: str,
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
):
    try:
        cm.delete_mapping(tenant_id, mapping_id)
    except Exception as exc:
        return _error(exc, 500)
    return {"status": "ok"}


@router.get("/api/v1/hotel/channels/allotments")
@require_any_permission("hotel.manage", "rooms.write")
async def get_allotments(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
    hotel_id: str = Query(...),
    start_date: str = Query(...),
    end_date: str = Query(...),
    ota: str | None = Query(default=None),
    room_type_id: str | None = Query(default=None),
):
    try:
        rows = cm.list_allotments(
            tenant_id,
            hotel_id,
            start_date=start_date,
            end_date=end_date,
            ota=ota,
            room_type_id=room_type_id,
        )
    except ValueError as exc:
        return _error(exc, 400)
    except Exception as exc:
        return _error(exc, 500)
    return {"status": "ok", "allotments": rows}


@router.put("/api/v1/hotel/channels/allotments")
@require_permission("hotel.manage")
async def put_allotments(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
):
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        result = cm.upsert_allotments(tenant_id, body or {})
    except ValueError as exc:
        return _error(exc, 400)
    except Exception as exc:
        return _error(exc, 500)
    return {"status": "ok", **result}


@router.post("/api/v1/hotel/channels/sync")
@require_permission("hotel.manage")
async def sync_now(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
):
    try:
        body = await request.json()
    except Exception:
        body = {}
    hotel_id = None
    ota = None
    if isinstance(body, dict):
        hotel_id = body.get("hotel_id") or body.get("property_id")
        ota = body.get("ota")
    try:
        result = cm.push_outbound(
            tenant_id,
            hotel_id=hotel_id or None,
            ota=ota or None,
            reason="manual" if not ota else "retry",
        )
    except Exception as exc:
        return _error(exc, 500)
    return result


@router.post("/api/v1/integrations/ota/webhook")
async def ota_webhook(request: Request):
    """Inbound OTA reservation. Authenticated with the channel webhook secret."""
    raw = await request.body()
    if raw.lstrip().startswith(b"<"):
        return JSONResponse(
            {"status": "error", "message": "Send a JSON booking notification"},
            status_code=415,
        )
    result = cm.handle_webhook(raw, dict(request.headers))
    status = int(result.pop("http_status", 200))
    return JSONResponse(result, status_code=status)
