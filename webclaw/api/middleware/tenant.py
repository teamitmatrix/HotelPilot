"""Tenant middleware — bind JWT + property headers onto request.state."""
from fastapi import Request
from fastapi.responses import JSONResponse

from auth.jwt_utils import get_signing_secret, verify_token
import db
from tenant import (
    PROPERTY_HEADER,
    TENANT_HEADER,
    TenantIsolationError,
    apply_impersonation,
    bind_request_tenant,
    build_context,
    set_tenant_context,
)


async def tenant_middleware(request: Request, call_next):
    """Attach TenantContext when a valid access token is present.

    Runs for every path. Auth-exempt routes still benefit if a token is sent
    (e.g. /me). Missing/invalid tokens leave request.state.tenant unset; the
    auth middleware decides whether that is allowed.
    """
    request.state.user_id = getattr(request.state, "user_id", None)
    request.state.tenant_id = None
    request.state.group_id = None
    request.state.active_property_id = None
    request.state.assigned_properties = ()
    request.state.scope_all_properties = False
    request.state.is_superadmin = False
    request.state.tenant = None

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return await call_next(request)

    try:
        conn = db.get_connection()
        secret = get_signing_secret(conn)
        payload = verify_token(auth_header[7:], secret, expected_type="access")
        if not payload:
            return await call_next(request)

        user_id = payload.get("sub")
        if not user_id:
            return await call_next(request)

        # Prefer live DB over stale JWT claims so a tenant reassignment takes
        # effect before the 15-minute access token expires. Impersonation is
        # the exception: it is access-token-only and must honor the JWT claim.
        ctx = apply_impersonation(
            build_context(conn, user_id, payload.get("email")),
            payload,
        )
        ctx = bind_request_tenant(
            conn,
            ctx,
            request.headers.get(TENANT_HEADER),
            property_header=request.headers.get(PROPERTY_HEADER),
        )
        request.state.user_id = user_id
        request.state.tenant_id = ctx.tenant_id
        request.state.group_id = ctx.group_id
        request.state.active_property_id = ctx.active_property_id
        request.state.assigned_properties = ctx.assigned_properties
        request.state.scope_all_properties = ctx.scope_all_properties
        request.state.is_superadmin = ctx.is_superadmin
        request.state.tenant = ctx
        token = set_tenant_context(ctx)
        try:
            return await call_next(request)
        finally:
            from tenant import reset_tenant_context
            reset_tenant_context(token)
    except TenantIsolationError as exc:
        return JSONResponse(
            {"status": "error", "message": exc.message},
            status_code=exc.status_code,
        )
    except Exception:
        return await call_next(request)
