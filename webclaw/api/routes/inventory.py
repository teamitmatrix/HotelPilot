"""Availability grid and inventory restriction controls."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from auth.deps import require_hotel_tenant
from rbac import require_any_permission, require_permission
from services import inventory_controls as inv

router = APIRouter(prefix="/api/v1/hotel/inventory", tags=["inventory"])


def _error(exc: Exception, status: int = 400):
    return JSONResponse({"status": "error", "message": str(exc)}, status_code=status)


@router.get("/availability")
@require_any_permission("rooms.write", "reports.generate", "hotel.manage")
async def get_availability(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
    hotel_id: str = Query(...),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    days: int | None = Query(default=None),
):
    try:
        data = inv.availability_grid(
            tenant_id,
            hotel_id,
            start_date=start_date,
            end_date=end_date,
            days=days,
        )
    except ValueError as exc:
        return _error(exc, 400)
    except Exception as exc:
        return _error(exc, 500)
    return {"status": "ok", **data}


@router.get("/restrictions")
@require_any_permission("rooms.write", "hotel.manage")
async def get_restrictions(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
    hotel_id: str = Query(...),
    start_date: str = Query(...),
    end_date: str = Query(...),
    room_type_id: str | None = Query(default=None),
):
    try:
        controls = inv.list_controls(
            tenant_id,
            hotel_id,
            start_date=start_date,
            end_date=end_date,
            room_type_id=room_type_id,
        )
    except ValueError as exc:
        return _error(exc, 400)
    except Exception as exc:
        return _error(exc, 500)
    return {"status": "ok", "controls": controls}


@router.put("/restrictions")
@require_permission("rooms.write")
async def put_restrictions(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
):
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        result = inv.upsert_controls(tenant_id, body or {})
    except ValueError as exc:
        return _error(exc, 400)
    except Exception as exc:
        return _error(exc, 500)
    return {"status": "ok", **result}


@router.post("/restrictions/clear")
@require_permission("rooms.write")
async def clear_restrictions(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
):
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        result = inv.clear_controls(tenant_id, body or {})
    except ValueError as exc:
        return _error(exc, 400)
    except Exception as exc:
        return _error(exc, 500)
    return {"status": "ok", **result}


@router.get("/restriction-calendar")
@require_any_permission("rooms.write", "hotel.manage", "reports.generate")
async def get_restriction_calendar(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
    hotel_id: str = Query(...),
    start_date: str = Query(...),
    end_date: str = Query(...),
    room_type_id: str | None = Query(default=None),
    rate_plan_id: str | None = Query(default=None),
    ota: str | None = Query(default=None),
):
    try:
        data = inv.restriction_calendar(
            tenant_id,
            hotel_id,
            start_date=start_date,
            end_date=end_date,
            room_type_id=room_type_id,
            rate_plan_id=rate_plan_id,
            ota=ota,
        )
    except ValueError as exc:
        return _error(exc, 400)
    except Exception as exc:
        return _error(exc, 500)
    return {"status": "ok", **data}
