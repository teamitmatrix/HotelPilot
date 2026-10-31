"""Executive hospitality analytics — performance KPIs and exports."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response

from auth.deps import get_current_user, require_hotel_tenant
from rbac import require_any_permission
from services import analytics as ax
from tenant import TenantContext

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


def _error(exc: Exception, status: int = 400):
    return JSONResponse({"status": "error", "message": str(exc)}, status_code=status)


def _property_ids(ctx: TenantContext, tenant_id: str) -> list[str]:
    ids: list[str] = []
    if ctx.scope_all_properties and ctx.assigned_properties:
        ids.extend(str(p) for p in ctx.assigned_properties if p)
    elif ctx.group_id:
        ids.extend(ax.list_group_property_ids(ctx.group_id, fallback=[tenant_id]))
    if tenant_id not in ids:
        ids.insert(0, tenant_id)
    # de-dupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for pid in ids:
        if pid not in seen:
            seen.add(pid)
            out.append(pid)
    return out


def _payload(
    tenant_id: str,
    ctx: TenantContext,
    from_date: str | None,
    to_date: str | None,
) -> dict:
    return ax.performance(
        tenant_id,
        from_date=from_date,
        to_date=to_date,
        property_ids=_property_ids(ctx, tenant_id),
    )


@router.get("/performance")
@require_any_permission("reports.generate", "hotel.manage")
async def performance(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
):
    ctx = get_current_user(request)
    try:
        return _payload(tenant_id, ctx, from_date, to_date)
    except ValueError as exc:
        return _error(exc, 400)
    except Exception as exc:
        return _error(exc, 500)


@router.get("/performance/export.csv")
@require_any_permission("reports.generate", "hotel.manage")
async def export_csv(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
):
    ctx = get_current_user(request)
    try:
        payload = _payload(tenant_id, ctx, from_date, to_date)
        data = ax.export_csv(payload)
    except Exception as exc:
        return _error(exc, 500)
    filename = f"hotel-analytics-{payload['period']['from_date']}.csv"
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/expected-occupancy")
@require_any_permission("reports.generate", "hotel.manage")
async def expected_occupancy(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
    days: int = Query(default=14, ge=1, le=90),
    from_date: str | None = Query(default=None),
):
    try:
        return ax.expected_occupancy(tenant_id, days=days, from_date=from_date)
    except ValueError as exc:
        return _error(exc, 400)
    except Exception as exc:
        return _error(exc, 500)


@router.get("/channel-production")
@require_any_permission("reports.generate", "hotel.manage")
async def channel_production(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
):
    from services import p1_reports as p1
    try:
        return p1.channel_production(tenant_id, from_date=from_date, to_date=to_date)
    except ValueError as exc:
        return _error(exc, 400)
    except Exception as exc:
        return _error(exc, 500)


@router.get("/channel-commission")
@require_any_permission("reports.generate", "hotel.manage")
async def channel_commission(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
):
    from services import p1_reports as p1
    try:
        return p1.commission_estimate(tenant_id, from_date=from_date, to_date=to_date)
    except ValueError as exc:
        return _error(exc, 400)
    except Exception as exc:
        return _error(exc, 500)


@router.get("/pickup-pace")
@require_any_permission("reports.generate", "hotel.manage")
async def pickup_pace(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
    as_of: str | None = Query(default=None),
    days: int = Query(default=30, ge=1, le=90),
):
    from services import p1_reports as p1
    try:
        return p1.pickup_pace(tenant_id, as_of=as_of, days=days)
    except ValueError as exc:
        return _error(exc, 400)
    except Exception as exc:
        return _error(exc, 500)


@router.get("/forecast")
@require_any_permission("reports.generate", "hotel.manage")
async def forecast(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
    days: int = Query(default=30, ge=1, le=90),
    from_date: str | None = Query(default=None),
):
    from services import p1_reports as p1
    try:
        return p1.forward_forecast(tenant_id, days=days, from_date=from_date)
    except ValueError as exc:
        return _error(exc, 400)
    except Exception as exc:
        return _error(exc, 500)


@router.get("/cancellations-by-source")
@require_any_permission("reports.generate", "hotel.manage")
async def cancellations_by_source(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
):
    from services import p1_reports as p1
    try:
        return p1.cancellations_by_source(tenant_id, from_date=from_date, to_date=to_date)
    except ValueError as exc:
        return _error(exc, 400)
    except Exception as exc:
        return _error(exc, 500)


@router.get("/performance/export.pdf")
@require_any_permission("reports.generate", "hotel.manage")
async def export_pdf(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
):
    ctx = get_current_user(request)
    try:
        payload = _payload(tenant_id, ctx, from_date, to_date)
        data = ax.export_pdf(payload)
    except Exception as exc:
        return _error(exc, 500)
    filename = f"hotel-analytics-{payload['period']['from_date']}.pdf"
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
