"""Manual night audit trigger for Hotel Owner / Accountant."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from auth.deps import get_current_user, require_hotel_tenant
from jobs.night_audit import (
    collect_night_audit_exceptions,
    list_hotels_for_audit,
    resolve_business_date,
    run_night_audit,
    _erp,
    _init_hosp,
    _parse_settings,
    _night_audit_policy,
)
from rbac import require_permission

router = APIRouter(prefix="/api/v1/hotel/night-audit", tags=["night-audit"])


def _row_val(row, key: str, idx: int = 0):
    if row is None:
        return None
    if hasattr(row, "keys"):
        try:
            return row[key]
        except Exception:
            return None
    try:
        return row[idx]
    except Exception:
        return None


def _serialize_run(row) -> dict:
    summary = None
    raw = _row_val(row, "summary_json", 7)
    if raw:
        try:
            summary = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            summary = None
    return {
        "id": _row_val(row, "id", 0),
        "hotel_id": _row_val(row, "hotel_id", 1),
        "business_date": _row_val(row, "business_date", 2),
        "status": _row_val(row, "status", 3),
        "triggered_by": _row_val(row, "triggered_by", 4),
        "started_at": _row_val(row, "started_at", 5),
        "completed_at": _row_val(row, "completed_at", 6),
        "kpis": (summary or {}).get("kpis"),
        "exceptions": (summary or {}).get("exceptions") or [],
        "exception_count": (summary or {}).get("exception_count") or 0,
        "summary": summary,
        "has_pdf": bool(summary),
    }


class NightAuditRequest(BaseModel):
    business_date: str | None = Field(
        default=None,
        description="ISO date to close (defaults to hotel-local today)",
    )
    hotel_id: str | None = None
    send_email: bool = True
    force: bool = Field(
        default=False,
        description="Re-run even if a completed audit already exists for the date",
    )


@router.post("/run")
@require_permission("finance.night_audit")
async def run_night_audit_endpoint(
    request: Request,
    body: NightAuditRequest,
    tenant_id: str = Depends(require_hotel_tenant),
    user=Depends(get_current_user),
):
    triggered_by = getattr(user, "id", None) or getattr(user, "user_id", None) or "manual"
    if isinstance(user, dict):
        triggered_by = user.get("id") or user.get("user_id") or "manual"

    result = await run_night_audit(
        tenant_id=tenant_id,
        hotel_id=body.hotel_id,
        business_date=body.business_date,
        triggered_by=str(triggered_by),
        send_email=body.send_email,
        force=body.force,
    )
    if result.get("status") == "error":
        return JSONResponse(result, status_code=400)
    return result


@router.get("/policy")
@require_permission("finance.night_audit")
async def get_night_audit_policy(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
    hotel_id: str | None = None,
):
    """Current no-show fee / audit-hour policy from hotel settings."""
    hotels = list_hotels_for_audit(tenant_id)
    if hotel_id:
        hotels = [h for h in hotels if h["id"] == hotel_id]
    if not hotels:
        return JSONResponse({"status": "error", "message": "Hotel not found"}, 404)
    h = hotels[0]
    settings = _parse_settings(h.get("settings_json"))
    policy = _night_audit_policy(settings)
    return {
        "status": "ok",
        "hotel_id": h["id"],
        "hotel_name": h.get("name"),
        "policy": policy,
    }


@router.get("/exceptions")
@require_permission("finance.night_audit")
async def night_audit_exceptions(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
    hotel_id: str | None = None,
    business_date: str | None = Query(default=None),
):
    """Live exception queue for the business date (open folios, overstays, OOO)."""
    conn = _erp()
    if not conn:
        return JSONResponse({"status": "error", "message": "Database unavailable"}, 503)
    try:
        _init_hosp(conn)
        hotels = list_hotels_for_audit(tenant_id)
        if hotel_id:
            hotels = [h for h in hotels if h["id"] == hotel_id]
        if not hotels:
            return {"status": "ok", "business_date": business_date, "exceptions": [], "hotels": []}

        out = []
        for h in hotels:
            settings = _parse_settings(h.get("settings_json"))
            policy = _night_audit_policy(settings)
            bdate = resolve_business_date(
                h.get("timezone"),
                explicit=business_date,
                audit_hour=policy["audit_hour"],
            )
            items = collect_night_audit_exceptions(conn, tenant_id, h["id"], bdate)
            out.append(
                {
                    "hotel_id": h["id"],
                    "hotel_name": h.get("name"),
                    "business_date": bdate,
                    "exceptions": items,
                    "exception_count": len(items),
                }
            )
        flat = [e for block in out for e in block["exceptions"]]
        return {
            "status": "ok",
            "business_date": business_date or (out[0]["business_date"] if out else None),
            "hotels": out,
            "exceptions": flat,
            "exception_count": len(flat),
        }
    finally:
        conn.close()


@router.get("/latest")
@require_permission("finance.night_audit")
async def latest_night_audit(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
    hotel_id: str | None = None,
    limit: int = Query(default=5, ge=1, le=100),
):
    from jobs.night_audit import _erp, _init_hosp

    conn = _erp()
    if not conn:
        return JSONResponse({"status": "error", "message": "Database unavailable"}, 503)
    try:
        _init_hosp(conn)
        hotels = list_hotels_for_audit(tenant_id)
        hotel_ids = [h["id"] for h in hotels]
        if hotel_id:
            hotel_ids = [hotel_id] if hotel_id in hotel_ids else []
        if not hotel_ids:
            return {"status": "ok", "runs": []}
        placeholders = ",".join("?" * len(hotel_ids))
        rows = conn.execute(
            f"""SELECT id, hotel_id, business_date, status, triggered_by,
                       started_at, completed_at, summary_json
                FROM hospitalityclaw_night_audit_run
                WHERE tenant_id = ? AND hotel_id IN ({placeholders})
                ORDER BY COALESCE(completed_at, started_at) DESC
                LIMIT {int(limit)}""",
            (tenant_id, *hotel_ids),
        ).fetchall()
        return {"status": "ok", "runs": [_serialize_run(r) for r in rows]}
    finally:
        conn.close()


@router.get("/runs/{run_id}")
@require_permission("finance.night_audit")
async def get_night_audit_run(
    run_id: str,
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
):
    from jobs.night_audit import _erp, _init_hosp

    conn = _erp()
    if not conn:
        return JSONResponse({"status": "error", "message": "Database unavailable"}, 503)
    try:
        _init_hosp(conn)
        row = conn.execute(
            """SELECT id, hotel_id, business_date, status, triggered_by,
                      started_at, completed_at, summary_json
               FROM hospitalityclaw_night_audit_run
               WHERE tenant_id = ? AND id = ?""",
            (tenant_id, run_id),
        ).fetchone()
        if not row:
            return JSONResponse({"status": "error", "message": "Night audit run not found"}, 404)
        return {"status": "ok", "run": _serialize_run(row)}
    finally:
        conn.close()


@router.get("/runs/{run_id}/pdf")
@require_permission("finance.night_audit")
async def download_night_audit_pdf(
    run_id: str,
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
):
    """Rebuild and download the night audit PDF from the stored summary."""
    from jobs.night_audit import _erp, _init_hosp
    from jobs.night_audit_report import build_night_audit_pdf

    conn = _erp()
    if not conn:
        return JSONResponse({"status": "error", "message": "Database unavailable"}, 503)
    try:
        _init_hosp(conn)
        row = conn.execute(
            """SELECT id, business_date, status, summary_json
               FROM hospitalityclaw_night_audit_run
               WHERE tenant_id = ? AND id = ?""",
            (tenant_id, run_id),
        ).fetchone()
        if not row:
            return JSONResponse({"status": "error", "message": "Night audit run not found"}, 404)
        raw = _row_val(row, "summary_json", 3)
        if not raw:
            return JSONResponse(
                {"status": "error", "message": "No summary available to build PDF"},
                404,
            )
        try:
            summary = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            return JSONResponse({"status": "error", "message": "Invalid audit summary"}, 500)
        if not isinstance(summary, dict):
            return JSONResponse({"status": "error", "message": "Invalid audit summary"}, 500)
        summary.setdefault("run_id", run_id)
        summary.setdefault("business_date", _row_val(row, "business_date", 1))
        pdf = build_night_audit_pdf(summary)
        bdate = str(_row_val(row, "business_date", 1) or "report")
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="night-audit-{bdate}.pdf"',
            },
        )
    finally:
        conn.close()
