"""Dynamic pricing rules and yield evaluation routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from auth.deps import require_hotel_tenant
from rbac import require_permission
from services import revenue as rv

router = APIRouter(prefix="/api/v1/hotel/pricing", tags=["pricing"])


def _error(exc: Exception, status: int = 400):
    return JSONResponse({"status": "error", "message": str(exc)}, status_code=status)


@router.get("/rules")
@require_permission("rooms.write")
async def pricing_dashboard(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
):
    try:
        data = rv.dashboard(tenant_id)
    except Exception as exc:
        return _error(exc, 500)
    return {"status": "ok", **data}


@router.put("/rules")
@require_permission("rooms.write")
async def upsert_rule(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
):
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        saved = rv.save_rule(tenant_id, body or {})
    except ValueError as exc:
        return _error(exc, 400)
    except Exception as exc:
        return _error(exc, 500)
    return {"status": "ok", "rule": saved}


@router.patch("/rules/{rule_id}")
@require_permission("rooms.write")
async def patch_rule(
    rule_id: str,
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if "is_active" in (body or {}):
        active = str(body.get("is_active")).lower() not in ("0", "false", "no", "off")
        try:
            saved = rv.set_rule_active(tenant_id, rule_id, active)
        except ValueError as exc:
            return _error(exc, 404 if "not found" in str(exc).lower() else 400)
        except Exception as exc:
            return _error(exc, 500)
        return {"status": "ok", "rule": saved}
    body = dict(body or {})
    body["id"] = rule_id
    try:
        saved = rv.save_rule(tenant_id, body)
    except ValueError as exc:
        return _error(exc, 400)
    except Exception as exc:
        return _error(exc, 500)
    return {"status": "ok", "rule": saved}


@router.delete("/rules/{rule_id}")
@require_permission("rooms.write")
async def remove_rule(
    rule_id: str,
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
):
    try:
        rv.delete_rule(tenant_id, rule_id)
    except Exception as exc:
        return _error(exc, 500)
    return {"status": "ok"}


@router.post("/evaluate")
@require_permission("rooms.write")
async def evaluate_now(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
):
    try:
        body = await request.json()
    except Exception:
        body = {}
    hotel_id = None
    push = True
    if isinstance(body, dict):
        hotel_id = body.get("hotel_id") or body.get("property_id")
        if "push_channels" in body:
            push = str(body.get("push_channels")).lower() not in ("0", "false", "no")
    try:
        result = rv.evaluate_tenant(
            tenant_id,
            hotel_id=str(hotel_id) if hotel_id else None,
            push_channels=push,
        )
    except ValueError as exc:
        return _error(exc, 400)
    except Exception as exc:
        return _error(exc, 500)
    return result
