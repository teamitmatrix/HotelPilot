"""FastAPI auth dependencies — JWT tenant context for request handlers.

Middleware still does the JWT decode and sets the ContextVar. Routes that
need the bound hotel should Depend on these helpers instead of re-parsing
the token.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from tenant import TenantContext, get_tenant_context
from rbac import require_permission  # noqa: F401 — @require_permission("bookings.write")


def get_optional_tenant(request: Request) -> TenantContext | None:
    """Return the request-scoped tenant, or None for anonymous/first-run."""
    ctx = getattr(request.state, "tenant", None)
    if ctx is not None:
        return ctx
    return get_tenant_context()


def get_current_user(request: Request) -> TenantContext:
    """Require a valid access token (hotel staff or SuperAdmin)."""
    ctx = get_optional_tenant(request)
    if ctx is None or not ctx.user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    return ctx


def get_tenant_id(ctx: TenantContext = Depends(get_current_user)) -> str | None:
    """Hotel UUID for row isolation. None means SuperAdmin (unscoped)."""
    return ctx.isolation_id


def require_hotel_tenant(ctx: TenantContext = Depends(get_current_user)) -> str:
    """Fail closed for hotel-scoped routes that cannot run platform-wide."""
    if ctx.isolation_id:
        return ctx.isolation_id
    if ctx.is_superadmin:
        raise HTTPException(
            status_code=400,
            detail="tenant_id is required; pass company-id to act on a hotel",
        )
    raise HTTPException(
        status_code=403,
        detail="tenant_id is required; this account is not bound to a hotel",
    )


def reject_if_impersonating(
    ctx: TenantContext | None = None,
    *,
    request: Request | None = None,
    message: str = "This action is blocked while impersonating a hotel",
) -> None:
    """Block high-risk account/billing mutations during support impersonation."""
    active = ctx
    if active is None and request is not None:
        active = get_optional_tenant(request)
    if active is not None and getattr(active, "impersonating", False):
        raise HTTPException(status_code=403, detail=message)
