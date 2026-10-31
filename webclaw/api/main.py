#!/usr/bin/env python3
"""HotelPilot API — property management gateway.

Routes:
  GET/POST /api/v1/{skill}/{action}  — Execute a skill action
  GET      /api/v1/schema/skills     — List installed skills
  GET      /api/v1/health            — Health check
  POST     /api/v1/auth/*            — Authentication endpoints
"""
import os
import re

import env_loader
env_loader.load_api_env()
# Pin the engine to the local PMS tree so child skill processes never
# resolve through ~/.openclaw/skills/erpclaw.
os.environ["ERPCLAW_ENGINE_PATH"] = env_loader.resolve_erpclaw_engine_path()

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

import db
import rbac
from auth.routes import router as auth_router
from auth.jwt_utils import get_signing_secret, verify_token
from auth.deps import get_current_user, require_hotel_tenant  # noqa: F401
from skills.router import router as skills_router
from chat.routes import router as chat_router
from events import router as events_router
from adaptive.routes import router as adaptive_router
from rbac_routes import router as admin_router
from admin_platform import router as admin_platform_router
from admin_ops import router as admin_ops_router
from admin_ops import hotel_router as hotel_announcements_router
from admin_growth import router as admin_growth_router
from scim_routes import router as scim_router
from partner_console import router as partner_console_router
from partner_console import admin_extra as partner_admin_extra_router
from admin_onboarding import admin_router as admin_onboarding_router
from admin_onboarding import hotel_router as hotel_onboarding_router
from admin_api_platform import router as admin_api_platform_router
from alerts.routes import router as alerts_router
from routes.hotel_users import router as hotel_users_router
from routes.properties import router as properties_router
from routes.billing import router as billing_router
from routes.notifications import router as notifications_router
from routes.guest import router as guest_router, staff_router as guest_staff_router
from routes.night_audit import router as night_audit_router
from routes.channels import router as channels_router
from routes.revenue import router as revenue_router
from routes.analytics import router as analytics_router
from routes.inventory import router as inventory_router
from middleware.audit import audit_middleware
from middleware.request_id import request_id_middleware
from middleware.tenant import tenant_middleware
from tenant import TenantIsolationError

# Environment — defaults to production for safety
IS_DEV = os.environ.get("WEBCLAW_ENV", "production").lower() == "development"
CORS_ORIGIN = os.environ.get("WEBCLAW_CORS_ORIGIN", "http://localhost:3000")

# Skill name validation: lowercase letters, digits, hyphens — 2-64 chars
SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")

MAX_BODY_SIZE = 10 * 1024 * 1024  # 10 MB

# Fully exempt — no auth check at all
AUTH_EXEMPT_PREFIXES = (
    "/api/v1/auth/",
    "/api/v1/health",
    "/api/v1/schema/",
    "/api/v1/webhooks/",
    "/api/v1/billing/config",
    "/api/v1/guest/",
    "/api/v1/integrations/ota/webhook",
    "/api/v1/scim/",
    "/api/v1/admin/public/",
)

# Auth required (JWT validated, user_id set) but no skill-action RBAC check.
# These prefixes are first-party API modules, not OpenClaw skills.
AUTH_REQUIRED_NO_RBAC = (
    "/api/v1/chat/",
    "/api/v1/events",
    "/api/v1/adaptive/",
    "/api/v1/admin/",
    "/api/v1/alerts",
    "/api/v1/hotel/",
    "/api/v1/owner/",
    "/api/v1/billing/",
    "/api/v1/analytics/",
    "/api/v1/partner/",
)

# Never treat these first path segments as skill names (defense in depth).
NON_SKILL_API_SEGMENTS = frozenset({
    "auth", "health", "schema", "chat", "events", "adaptive",
    "admin", "alerts", "hotel", "owner", "billing", "webhooks", "guest",
    "integrations", "analytics", "scim", "partner",
})


from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Platform DB is Postgres-only.
    env_loader.require_platform_postgres()
    # Touch the platform connection so schema init runs before traffic.
    conn = db.get_connection()
    try:
        from admin_growth import ensure_growth_tables
        from admin_onboarding import ensure_onboarding_tables
        from admin_api_platform import ensure_api_platform_tables
        from admin_ops import ensure_ops_tables

        ensure_growth_tables(conn)
        ensure_onboarding_tables(conn)
        ensure_api_platform_tables(conn)
        ensure_ops_tables(conn)
    except Exception:
        pass

    from notifications.scheduler import start_scheduler, stop_scheduler
    from jobs.scheduler import (
        start_night_audit_scheduler,
        stop_night_audit_scheduler,
        start_yield_scheduler,
        stop_yield_scheduler,
        start_ops_scheduler,
        stop_ops_scheduler,
    )
    start_scheduler()
    start_night_audit_scheduler()
    start_yield_scheduler()
    start_ops_scheduler()
    try:
        yield
    finally:
        await stop_scheduler()
        await stop_night_audit_scheduler()
        await stop_yield_scheduler()
        await stop_ops_scheduler()


app = FastAPI(title="HotelPilot API", version="0.4.0", lifespan=_lifespan)


# ── Exception Handlers ──────────────────────────────────────────────────────

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    req_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        {"status": "error", "message": str(exc.detail), "request_id": req_id},
        status_code=exc.status_code,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    req_id = getattr(request.state, "request_id", None)
    errors = []
    for err in exc.errors():
        loc = " → ".join(str(l) for l in err.get("loc", []))
        errors.append({"field": loc, "message": err.get("msg", "")})
    return JSONResponse(
        {"status": "error", "message": "Validation error", "errors": errors, "request_id": req_id},
        status_code=422,
    )


@app.exception_handler(TenantIsolationError)
async def tenant_isolation_handler(request: Request, exc: TenantIsolationError):
    req_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        {"status": "error", "message": exc.message, "request_id": req_id},
        status_code=exc.status_code,
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    req_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        {"status": "error", "message": "Internal server error", "request_id": req_id},
        status_code=500,
    )


# CORS — never use "*" with credentials; browsers reject that combination.
# localhost and 127.0.0.1 are different origins. Always allow both local
# frontends so EventSource (live updates) can reach FastAPI directly in dev.
_cors_origins = [CORS_ORIGIN]
for origin in ("http://localhost:3000", "http://127.0.0.1:3000"):
    if origin not in _cors_origins:
        _cors_origins.append(origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(events_router)
app.include_router(adaptive_router)
app.include_router(admin_router)
app.include_router(admin_platform_router)
app.include_router(admin_ops_router)
app.include_router(hotel_announcements_router)
app.include_router(admin_growth_router)
app.include_router(scim_router)
app.include_router(partner_console_router)
app.include_router(partner_admin_extra_router)
app.include_router(admin_onboarding_router)
app.include_router(hotel_onboarding_router)
app.include_router(admin_api_platform_router)
app.include_router(alerts_router)
app.include_router(hotel_users_router)
app.include_router(properties_router)
app.include_router(billing_router)
app.include_router(notifications_router)
app.include_router(guest_router)
app.include_router(guest_staff_router)
app.include_router(night_audit_router)
app.include_router(channels_router)
app.include_router(revenue_router)
app.include_router(analytics_router)
app.include_router(inventory_router)
app.include_router(skills_router)


# ── Middleware ────────────────────────────────────────────────────────────────

@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """Auth + RBAC + payload validation for all skill routes."""
    path = request.url.path

    # 1. Payload size limit
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_BODY_SIZE:
        return JSONResponse(
            {"status": "error", "message": "Request body too large (max 10MB)"},
            status_code=413,
        )

    # 2. Skip auth for exempt paths
    if any(path.startswith(prefix) for prefix in AUTH_EXEMPT_PREFIXES):
        return await call_next(request)

    # 3. JWT auth only (no RBAC) for chat/events/adaptive
    if any(path.startswith(prefix) for prefix in AUTH_REQUIRED_NO_RBAC):
        # These routes handle their own auth internally
        return await call_next(request)

    # 4. Auth + RBAC for skill routes only (/{skill}/{action})
    match = re.match(r"^/api/v1/([^/]+)/([^/]+)$", path)
    if match:
        skill, action = match.group(1), match.group(2)
        if skill in NON_SKILL_API_SEGMENTS:
            return await call_next(request)

        if not SKILL_NAME_RE.match(skill):
            return JSONResponse(
                {"status": "error", "message": f"Invalid skill name: {skill}"},
                status_code=400,
            )

        conn = db.get_connection()
        user_count = conn.execute("SELECT COUNT(*) FROM webclaw_user").fetchone()[0]
        if user_count == 0:
            request.state.user_id = None
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                {"status": "error", "message": "Authentication required"},
                status_code=401,
            )

        secret = get_signing_secret(conn)
        payload = verify_token(auth_header[7:], secret, expected_type="access")
        if not payload:
            return JSONResponse(
                {"status": "error", "message": "Invalid or expired token"},
                status_code=401,
            )

        user_id = payload["sub"]
        if not rbac.check_permission(conn, user_id, skill, action):
            return JSONResponse(
                {"status": "error", "message": f"Permission denied: {action} on {skill}"},
                status_code=403,
            )

        request.state.user_id = user_id

    return await call_next(request)


# Tenant context (JWT → request.state.tenant_id) before audit so logs can use it
app.middleware("http")(tenant_middleware)

# Audit middleware (logs POST requests to skill routes)
app.middleware("http")(audit_middleware)

# Request ID middleware (outermost — runs first, sets request.state.request_id)
app.middleware("http")(request_id_middleware)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/api/v1/health")
async def health():
    return {"status": "ok", "service": "ocui-api"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001)
