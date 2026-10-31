"""Alerts API — evaluates alert rules against live skill data."""
import time

from fastapi import APIRouter, Depends, Request

from skills.executor import execute_skill, iter_skills
from .alert_rules import get_rules_for_skills, _check_condition
from auth.deps import get_optional_tenant
from tenant import TenantContext

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])

# Simple cache: (timestamp, alerts_list)
_cache: tuple[float, list[dict]] | None = None
CACHE_TTL = 300  # 5 minutes

# Company ID cache (rarely changes)
_company_id_cache: tuple[float, str | None] | None = None
_COMPANY_CACHE_TTL = 3600  # 1 hour

def _get_installed_skills() -> set[str]:
    """Discover installed skill names from the local PMS skill tree."""
    return {name for name, _ in iter_skills()}


def _get_default_company_id() -> str | None:
    """Read the first company ID from the ERPClaw database."""
    global _company_id_cache
    now = time.time()
    if _company_id_cache and (now - _company_id_cache[0]) < _COMPANY_CACHE_TTL:
        return _company_id_cache[1]

    try:
        import db as dbmod

        conn = dbmod.get_skill_db("erpclaw")
        if conn is None:
            _company_id_cache = (now, None)
            return None
        try:
            row = conn.execute("SELECT id FROM company LIMIT 1").fetchone()
        finally:
            conn.close()
        company_id = row[0] if row else None
        _company_id_cache = (now, company_id)
        return company_id
    except Exception:
        _company_id_cache = (now, None)
        return None


def _extract_count(result: dict, field_name: str) -> float | None:
    """Extract a numeric count from skill result, handling various formats."""
    # Direct field
    raw = result.get(field_name)
    if raw is not None:
        try:
            return float(raw)
        except (ValueError, TypeError):
            pass

    # Fallback: count items in a list field (e.g., "work_orders", "leases", etc.)
    for key, val in result.items():
        if isinstance(val, list):
            return float(len(val))

    return None


@router.get("")
async def get_alerts(
    request: Request,
    tenant: TenantContext | None = Depends(get_optional_tenant),
):
    """Evaluate alert rules and return fired alerts.

    Results are cached for 5 minutes to avoid excessive skill calls.
    Hotel staff only see rules evaluated against their own company.
    """
    global _cache

    # Check cache
    now = time.time()
    if _cache and (now - _cache[0]) < CACHE_TTL:
        return {"status": "ok", "alerts": _cache[1], "cached": True}

    installed = _get_installed_skills()
    rules = get_rules_for_skills(installed)

    isolation = tenant.isolation_id if tenant else None
    is_superadmin = bool(tenant.is_superadmin) if tenant else False
    # SuperAdmin may fall back to the first company; hotel staff never do.
    company_id = isolation
    if company_id is None and is_superadmin and any(r.needs_company_id for r in rules):
        company_id = _get_default_company_id()

    alerts: list[dict] = []
    for rule in rules:
        try:
            params = {**rule.params, "limit": "0"}

            if rule.needs_company_id and company_id:
                params["company-id"] = company_id
                params["tenant-id"] = company_id

            result = await execute_skill(
                rule.skill,
                rule.action,
                params,
                tenant_id=company_id,
                is_superadmin=is_superadmin,
            )
            if result.get("status") != "ok":
                continue

            value = _extract_count(result, rule.field)
            if value is None:
                continue

            if _check_condition(value, rule.condition, rule.threshold):
                alerts.append({
                    "severity": rule.severity,
                    "message": rule.message_template.format(value=int(value)),
                    "count": int(value),
                    "skill": rule.skill,
                    "link": rule.link_path,
                })
        except Exception:
            continue  # Non-critical — skip failed rules

    _cache = (now, alerts)
    return {"status": "ok", "alerts": alerts, "cached": False}
