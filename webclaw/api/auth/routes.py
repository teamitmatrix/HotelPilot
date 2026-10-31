"""Auth API routes: login, refresh, logout, me, change-password, setup."""
import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from .passwords import hash_password, verify_password, validate_password_strength
from .jwt_utils import (
    create_access_token,
    create_refresh_token,
    get_signing_secret,
    hash_token,
    issue_access_token,
    verify_token,
    REFRESH_TOKEN_EXPIRY,
)
import db

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

IS_DEV = os.environ.get("WEBCLAW_ENV", "production") != "production"
# Only set Secure flag when explicitly enabled (i.e., behind HTTPS termination)
USE_SECURE_COOKIE = os.environ.get("WEBCLAW_HTTPS", "").lower() in ("1", "true", "yes")

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION = timedelta(minutes=15)
MAX_SESSIONS_PER_USER = 5


def _set_refresh_cookie(response: Response, token: str):
    """Set the httpOnly refresh token cookie."""
    response.set_cookie(
        key="refresh_token",
        value=token,
        httponly=True,
        secure=USE_SECURE_COOKIE,
        samesite="lax",
        max_age=int(REFRESH_TOKEN_EXPIRY.total_seconds()),
        path="/",
    )


def _clear_refresh_cookie(response: Response):
    """Clear the refresh token cookie."""
    response.delete_cookie(
        key="refresh_token",
        path="/",
        httponly=True,
        secure=USE_SECURE_COOKIE,
        samesite="lax",
    )


def _get_client_info(request: Request) -> tuple[str, str]:
    """Extract IP and user-agent from request."""
    ip = request.headers.get("x-real-ip", request.client.host if request.client else "unknown")
    ua = request.headers.get("user-agent", "")[:256]
    return ip, ua


@router.post("/login")
async def login(request: Request):
    """Authenticate with email + password, return access token + refresh cookie."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid request body"}, 400)

    from models import LoginRequest
    from pydantic import ValidationError
    try:
        req = LoginRequest(**body)
    except ValidationError as e:
        return JSONResponse({"status": "error", "message": str(e.errors()[0]["msg"])}, 400)

    email = req.email
    password = req.password

    conn = db.get_connection()
    ip, ua = _get_client_info(request)

    # Platform IP allowlist (when enabled) — blocks before credential checks
    try:
        from admin_ops import check_ip_allowlist, notify_ops

        ip_check = check_ip_allowlist(ip, conn)
        if ip_check.get("enabled") and not ip_check.get("allowed"):
            try:
                notify_ops(
                    "login_ip_blocked",
                    {"email": email, "ip": ip, "user_agent": (ua or "")[:200]},
                    severity="critical",
                    title="Login blocked by IP allowlist",
                )
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return JSONResponse(
                {
                    "status": "error",
                    "message": "Access denied from this network location",
                    "code": "IP_NOT_ALLOWED",
                },
                403,
            )
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass

    max_failed = MAX_FAILED_ATTEMPTS
    try:
        from admin_ops import _load_security

        pol = _load_security(conn)
        max_failed = int(pol.get("lockout_after_failures") or MAX_FAILED_ATTEMPTS)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        max_failed = MAX_FAILED_ATTEMPTS

    # Lookup user
    user = conn.execute(
        "SELECT id, email, full_name, password_hash, status, failed_login_attempts, locked_until FROM webclaw_user WHERE email = ?",
        (email,),
    ).fetchone()

    if not user:
        return JSONResponse({"status": "error", "message": "Invalid credentials"}, 401)

    user_id = user[0] if isinstance(user, tuple) else user["id"]
    user_email = user[1] if isinstance(user, tuple) else user["email"]
    full_name = user[2] if isinstance(user, tuple) else user["full_name"]
    pw_hash = user[3] if isinstance(user, tuple) else user["password_hash"]
    status = user[4] if isinstance(user, tuple) else user["status"]
    failed_attempts = (user[5] if isinstance(user, tuple) else user["failed_login_attempts"]) or 0
    locked_until = user[6] if isinstance(user, tuple) else user["locked_until"]

    if status != "active":
        return JSONResponse({"status": "error", "message": "Account is disabled"}, 401)

    # Maintenance mode blocks hotel staff logins; platform operators may still enter.
    try:
        from admin_ops import get_maintenance_state
        from tenant import build_context

        maint = get_maintenance_state(conn)
        if maint.get("maintenance_mode"):
            probe = build_context(conn, user_id, user_email)
            if not probe.is_superadmin:
                from rbac import has_permission

                is_platform_ops = has_permission(
                    conn,
                    user_id,
                    "platform.admin",
                    is_superadmin=False,
                ) or any(
                    has_permission(conn, user_id, perm, is_superadmin=False)
                    for perm in (
                        "platform.tenants.view",
                        "platform.support.manage",
                        "platform.billing.manage",
                        "platform.security.manage",
                        "platform.settings.manage",
                        "platform.audit.view",
                    )
                )
                if not is_platform_ops:
                    return JSONResponse(
                        {
                            "status": "error",
                            "message": maint.get("maintenance_message")
                            or "Platform is under maintenance",
                            "maintenance_mode": True,
                        },
                        503,
                    )
    except Exception:
        pass

    # Check lockout
    if locked_until:
        lock_time = datetime.fromisoformat(locked_until)
        if datetime.now(timezone.utc) < lock_time:
            return JSONResponse({"status": "error", "message": "Account temporarily locked. Try again later."}, 429)
        # Lockout expired — reset
        conn.execute(
            "UPDATE webclaw_user SET failed_login_attempts = 0, locked_until = NULL WHERE id = ?",
            (user_id,),
        )
        conn.commit()
        failed_attempts = 0

    if not pw_hash:
        return JSONResponse({"status": "error", "message": "No web password set for this account"}, 401)

    if not verify_password(password, pw_hash):
        failed_attempts += 1
        if failed_attempts >= max_failed:
            lock_until = (datetime.now(timezone.utc) + LOCKOUT_DURATION).isoformat()
            conn.execute(
                "UPDATE webclaw_user SET failed_login_attempts = ?, locked_until = ? WHERE id = ?",
                (failed_attempts, lock_until, user_id),
            )
            try:
                from admin_ops import notify_ops

                notify_ops(
                    "login_lockout",
                    {
                        "user_id": user_id,
                        "email": user_email,
                        "ip": ip,
                        "failed_attempts": failed_attempts,
                    },
                    severity="critical",
                    title="Account locked after failed logins",
                )
            except Exception:
                pass
        else:
            conn.execute(
                "UPDATE webclaw_user SET failed_login_attempts = ? WHERE id = ?",
                (failed_attempts, user_id),
            )
            if failed_attempts >= max(2, max_failed - 1):
                try:
                    from admin_ops import notify_ops

                    notify_ops(
                        "login_anomaly",
                        {
                            "user_id": user_id,
                            "email": user_email,
                            "ip": ip,
                            "failed_attempts": failed_attempts,
                            "reason": "burst_failures",
                        },
                        severity="warning",
                        title="Repeated failed login attempts",
                    )
                except Exception:
                    pass
        conn.commit()
        return JSONResponse({"status": "error", "message": "Invalid credentials"}, 401)

    # Reset failed attempts on successful login
    if failed_attempts > 0:
        conn.execute(
            "UPDATE webclaw_user SET failed_login_attempts = 0, locked_until = NULL WHERE id = ?",
            (user_id,),
        )

    # New IP anomaly (prior sessions exist from another location)
    try:
        prior = conn.execute(
            """SELECT ip_address FROM webclaw_session
               WHERE user_id = ? AND ip_address IS NOT NULL AND ip_address != ''
               LIMIT 20""",
            (user_id,),
        ).fetchall()
        known_ips = {
            (p[0] if isinstance(p, tuple) else p["ip_address"]) for p in prior
        }
        if known_ips and ip and ip not in known_ips:
            from admin_ops import notify_ops

            notify_ops(
                "login_anomaly",
                {
                    "user_id": user_id,
                    "email": user_email,
                    "ip": ip,
                    "known_ips": list(known_ips)[:5],
                    "reason": "new_ip",
                },
                severity="warning",
                title="Login from new IP address",
            )
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass

    # Create tokens
    secret = get_signing_secret(conn)
    access_token = issue_access_token(conn, user_id, user_email)
    raw_refresh, refresh_hash = create_refresh_token(user_id, secret)

    # Store session
    conn.execute(
        """INSERT INTO webclaw_session (id, user_id, refresh_token_hash, expires_at, ip_address, user_agent)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()),
            user_id,
            refresh_hash,
            (datetime.now(timezone.utc) + REFRESH_TOKEN_EXPIRY).isoformat(),
            ip,
            ua,
        ),
    )
    conn.execute(
        "UPDATE webclaw_user SET last_login = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), user_id),
    )

    # Session cap — evict oldest sessions beyond limit
    sessions = conn.execute(
        "SELECT id FROM webclaw_session WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()
    if len(sessions) > MAX_SESSIONS_PER_USER:
        old_ids = [s[0] if isinstance(s, tuple) else s["id"] for s in sessions[MAX_SESSIONS_PER_USER:]]
        placeholders = ",".join("?" for _ in old_ids)
        conn.execute(f"DELETE FROM webclaw_session WHERE id IN ({placeholders})", old_ids)

    from tenant import build_context, public_user
    ctx = build_context(conn, user_id, user_email)

    conn.commit()

    response = JSONResponse({
        "status": "ok",
        "access_token": access_token,
        "user": public_user(conn, ctx, full_name=full_name),
    })
    _set_refresh_cookie(response, raw_refresh)
    return response


@router.post("/refresh")
async def refresh(request: Request):
    """Rotate refresh token, return new access token."""
    raw_token = request.cookies.get("refresh_token")
    if not raw_token:
        return JSONResponse({"status": "error", "message": "No refresh token"}, 401)

    conn = db.get_connection()
    secret = get_signing_secret(conn)

    # Verify JWT signature + expiry
    payload = verify_token(raw_token, secret, expected_type="refresh")
    if not payload:
        response = JSONResponse({"status": "error", "message": "Invalid or expired refresh token"}, 401)
        _clear_refresh_cookie(response)
        return response

    # Lookup session by token hash
    token_hash = hash_token(raw_token)
    session = conn.execute(
        "SELECT id, user_id FROM webclaw_session WHERE refresh_token_hash = ?", (token_hash,)
    ).fetchone()

    if not session:
        response = JSONResponse({"status": "error", "message": "Session not found (token revoked)"}, 401)
        _clear_refresh_cookie(response)
        return response

    session_id = session[0] if isinstance(session, tuple) else session["id"]
    user_id = session[1] if isinstance(session, tuple) else session["user_id"]

    # Get user info
    user = conn.execute(
        "SELECT email, full_name, status FROM webclaw_user WHERE id = ?", (user_id,)
    ).fetchone()

    if not user or (user[2] if isinstance(user, tuple) else user["status"]) != "active":
        conn.execute("DELETE FROM webclaw_session WHERE id = ?", (session_id,))
        conn.commit()
        response = JSONResponse({"status": "error", "message": "Account disabled"}, 401)
        _clear_refresh_cookie(response)
        return response

    user_email = user[0] if isinstance(user, tuple) else user["email"]

    # Rotate: delete old session, create new
    conn.execute("DELETE FROM webclaw_session WHERE id = ?", (session_id,))

    new_access = issue_access_token(conn, user_id, user_email)
    new_raw_refresh, new_refresh_hash = create_refresh_token(user_id, secret)

    ip, ua = _get_client_info(request)
    conn.execute(
        """INSERT INTO webclaw_session (id, user_id, refresh_token_hash, expires_at, ip_address, user_agent)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()),
            user_id,
            new_refresh_hash,
            (datetime.now(timezone.utc) + REFRESH_TOKEN_EXPIRY).isoformat(),
            ip,
            ua,
        ),
    )
    conn.commit()

    response = JSONResponse({"status": "ok", "access_token": new_access})
    _set_refresh_cookie(response, new_raw_refresh)
    return response


@router.post("/logout")
async def logout(request: Request):
    """Delete session, clear refresh cookie."""
    raw_token = request.cookies.get("refresh_token")
    if raw_token:
        conn = db.get_connection()
        token_hash = hash_token(raw_token)
        conn.execute("DELETE FROM webclaw_session WHERE refresh_token_hash = ?", (token_hash,))
        conn.commit()

    response = JSONResponse({"status": "ok", "message": "Logged out"})
    _clear_refresh_cookie(response)
    return response


@router.get("/me")
async def me(request: Request):
    """Return current user info + roles. Requires Bearer token."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse({"status": "error", "message": "Authentication required"}, 401)

    conn = db.get_connection()
    secret = get_signing_secret(conn)
    payload = verify_token(auth_header[7:], secret, expected_type="access")
    if not payload:
        return JSONResponse({"status": "error", "message": "Invalid or expired token"}, 401)

    user_id = payload["sub"]
    user = conn.execute(
        "SELECT id, email, full_name, username, status FROM webclaw_user WHERE id = ?",
        (user_id,),
    ).fetchone()

    if not user:
        return JSONResponse({"status": "error", "message": "User not found"}, 404)

    from tenant import apply_impersonation, build_context, public_user
    email = user[1] if isinstance(user, tuple) else user["email"]
    bound = getattr(request.state, "tenant", None)
    if bound is not None and bound.user_id == user_id:
        ctx = bound
    else:
        ctx = apply_impersonation(build_context(conn, user_id, email), payload)

    hotel_name = None
    if ctx.impersonating and ctx.tenant_id:
        skill_db = db.get_skill_db("erpclaw")
        if skill_db:
            try:
                row = skill_db.execute(
                    "SELECT hotel_name FROM pms_tenant WHERE id = ? OR company_id = ?",
                    (ctx.tenant_id, ctx.tenant_id),
                ).fetchone()
                if row:
                    hotel_name = row[0] if not hasattr(row, "keys") else row["hotel_name"]
            except Exception:
                hotel_name = None
            finally:
                skill_db.close()

    return {
        "status": "ok",
        "user": public_user(
            conn,
            ctx,
            full_name=user[2] if isinstance(user, tuple) else user["full_name"],
            username=user[3] if isinstance(user, tuple) else user["username"],
            impersonated_hotel=hotel_name,
        ),
    }


@router.post("/switch-tenant")
async def switch_tenant(request: Request):
    """Activate another hotel the signed-in user already belongs to.

    Does not change the account's home tenant. Later requests must send
    ``X-Property-ID`` (or legacy ``X-Tenant-Id``) so every API call stays
    bound to the selected property. Pass ``ALL`` for group-wide reads.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse({"status": "error", "message": "Authentication required"}, 401)

    conn = db.get_connection()
    secret = get_signing_secret(conn)
    payload = verify_token(auth_header[7:], secret, expected_type="access")
    if not payload:
        return JSONResponse({"status": "error", "message": "Invalid or expired token"}, 401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid request body"}, 400)

    from models import SwitchTenantRequest
    from pydantic import ValidationError
    try:
        req = SwitchTenantRequest(**body)
    except ValidationError as e:
        return JSONResponse({"status": "error", "message": str(e.errors()[0]["msg"])}, 400)

    from tenant import (
        TenantIsolationError,
        bind_request_tenant,
        build_context,
        public_user,
    )
    user_id = payload["sub"]
    base = build_context(conn, user_id, payload.get("email"))
    if base.is_superadmin:
        return JSONResponse(
            {
                "status": "error",
                "message": "Platform admins enter a hotel with impersonation, not the property switcher",
            },
            403,
        )

    try:
        switched = bind_request_tenant(
            conn,
            base,
            None if req.tenant_id.upper() == "ALL" else req.tenant_id,
            property_header=req.tenant_id,
        )
    except TenantIsolationError as exc:
        return JSONResponse(
            {"status": "error", "message": exc.message},
            status_code=exc.status_code,
        )

    row = conn.execute(
        "SELECT full_name, username FROM webclaw_user WHERE id = ?",
        (user_id,),
    ).fetchone()
    full_name = None
    username = None
    if row:
        full_name = row[0] if isinstance(row, tuple) else row["full_name"]
        username = row[1] if isinstance(row, tuple) else row["username"]

    return {
        "status": "ok",
        "user": public_user(conn, switched, full_name=full_name, username=username),
    }


@router.post("/impersonate/end")
async def end_impersonation(request: Request):
    """Audit the end of a support impersonation session (does not need platform.admin)."""
    from auth.deps import get_optional_tenant
    from admin_ops import write_platform_audit

    ctx = get_optional_tenant(request)
    if ctx is None or not ctx.user_id:
        return JSONResponse({"status": "error", "message": "Authentication required"}, 401)
    if not getattr(ctx, "impersonating", False):
        return JSONResponse({"status": "error", "message": "Not currently impersonating"}, 400)

    conn = db.get_connection()
    write_platform_audit(
        conn,
        user_id=ctx.user_id,
        user_email=ctx.email,
        tenant_id=ctx.tenant_id,
        action_category="security",
        action="impersonation_ended",
        ip_address=request.client.host if request.client else None,
        detail=(getattr(ctx, "impersonation_reason", None) or "")[:500],
    )
    return {"status": "ok", "message": "Impersonation ended"}


@router.put("/profile")
async def update_profile(request: Request):
    """Update the authenticated user's name, email, and optionally password."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse({"status": "error", "message": "Authentication required"}, 401)

    conn = db.get_connection()
    secret = get_signing_secret(conn)
    payload = verify_token(auth_header[7:], secret, expected_type="access")
    if not payload:
        return JSONResponse({"status": "error", "message": "Invalid or expired token"}, 401)

    if payload.get("is_impersonating") or payload.get("impersonate_tenant_id"):
        return JSONResponse(
            {
                "status": "error",
                "message": "Profile and password changes are blocked while impersonating",
            },
            403,
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid request body"}, 400)

    from models import UpdateProfileRequest
    from pydantic import ValidationError
    try:
        req = UpdateProfileRequest(**body)
    except ValidationError as e:
        msg = e.errors()[0].get("msg", "Invalid input") if e.errors() else "Invalid input"
        return JSONResponse({"status": "error", "message": str(msg)}, 400)

    user_id = payload["sub"]
    row = conn.execute(
        "SELECT id, email, full_name, username, password_hash, status FROM webclaw_user WHERE id = ?",
        (user_id,),
    ).fetchone()
    if not row:
        return JSONResponse({"status": "error", "message": "User not found"}, 404)

    if isinstance(row, tuple):
        cur_email, cur_name, cur_username, pw_hash = row[1], row[2], row[3], row[4]
        status = row[5]
    else:
        cur_email = row["email"]
        cur_name = row["full_name"]
        cur_username = row["username"]
        pw_hash = row["password_hash"]
        status = row["status"]

    if status != "active":
        return JSONResponse({"status": "error", "message": "Account is not active"}, 403)

    new_name = req.full_name if req.full_name is not None else cur_name
    new_email = req.email if req.email is not None else cur_email
    if not new_name:
        return JSONResponse({"status": "error", "message": "Full name is required"}, 400)
    if not new_email or "@" not in new_email:
        return JSONResponse({"status": "error", "message": "A valid email is required"}, 400)

    if new_email != cur_email:
        clash = conn.execute(
            "SELECT id FROM webclaw_user WHERE email = ? AND id != ?",
            (new_email, user_id),
        ).fetchone()
        if clash:
            return JSONResponse(
                {"status": "error", "message": "That email is already in use"},
                409,
            )

    requires_reauth = False
    if req.new_password:
        pw_err = validate_password_strength(req.new_password)
        if pw_err:
            return JSONResponse({"status": "error", "message": pw_err}, 400)
        if not verify_password(req.current_password or "", pw_hash):
            return JSONResponse(
                {"status": "error", "message": "Current password is incorrect"},
                401,
            )
        conn.execute(
            "UPDATE webclaw_user SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (hash_password(req.new_password), user_id),
        )
        conn.execute("DELETE FROM webclaw_session WHERE user_id = ?", (user_id,))
        requires_reauth = True

    # Keep username in sync with email local-part when email changes
    new_username = cur_username
    if new_email != cur_email:
        base = new_email.split("@")[0][:64] or "user"
        new_username = base
        taken = conn.execute(
            "SELECT id FROM webclaw_user WHERE username = ? AND id != ?",
            (new_username, user_id),
        ).fetchone()
        if taken:
            new_username = f"{base}-{user_id[:8]}"

    conn.execute(
        """UPDATE webclaw_user
           SET full_name = ?, email = ?, username = ?, updated_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (new_name, new_email, new_username, user_id),
    )
    conn.commit()

    try:
        from admin_ops import write_platform_audit
        from tenant import build_context

        ctx = build_context(conn, user_id, new_email)
        if ctx.is_superadmin:
            forwarded = request.headers.get("x-forwarded-for")
            ip = (
                forwarded.split(",")[0].strip()
                if forwarded
                else (request.client.host if request.client else None)
            )
            write_platform_audit(
                conn,
                user_id=user_id,
                user_email=new_email,
                tenant_id=None,
                action_category="security",
                action="update_profile",
                ip_address=ip,
                detail="name/email" + ("+password" if requires_reauth else ""),
            )
    except Exception:
        pass

    return {
        "status": "ok",
        "message": (
            "Profile updated. Please log in again with your new password."
            if requires_reauth
            else "Profile updated."
        ),
        "requires_reauth": requires_reauth,
        "user": {
            "user_id": user_id,
            "email": new_email,
            "full_name": new_name,
            "username": new_username,
        },
    }


@router.post("/change-password")
async def change_password(request: Request):
    """Change password. Requires Bearer token + current password."""
    auth_peek = request.headers.get("Authorization", "")
    if auth_peek.startswith("Bearer "):
        conn_peek = db.get_connection()
        payload_peek = verify_token(
            auth_peek[7:], get_signing_secret(conn_peek), expected_type="access"
        )
        if payload_peek and (
            payload_peek.get("is_impersonating") or payload_peek.get("impersonate_tenant_id")
        ):
            return JSONResponse(
                {
                    "status": "error",
                    "message": "Password changes are blocked while impersonating",
                },
                403,
            )
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse({"status": "error", "message": "Authentication required"}, 401)

    conn = db.get_connection()
    secret = get_signing_secret(conn)
    payload = verify_token(auth_header[7:], secret, expected_type="access")
    if not payload:
        return JSONResponse({"status": "error", "message": "Invalid or expired token"}, 401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid request body"}, 400)

    from models import ChangePasswordRequest
    from pydantic import ValidationError
    try:
        req = ChangePasswordRequest(**body)
    except ValidationError as e:
        return JSONResponse({"status": "error", "message": str(e.errors()[0]["msg"])}, 400)

    current_pw = req.current_password
    new_pw = req.new_password

    pw_err = validate_password_strength(new_pw)
    if pw_err:
        return JSONResponse({"status": "error", "message": pw_err}, 400)

    user_id = payload["sub"]
    user = conn.execute(
        "SELECT password_hash FROM webclaw_user WHERE id = ?", (user_id,)
    ).fetchone()

    if not user:
        return JSONResponse({"status": "error", "message": "User not found"}, 404)

    pw_hash = user[0] if isinstance(user, tuple) else user["password_hash"]
    if not verify_password(current_pw, pw_hash):
        return JSONResponse({"status": "error", "message": "Current password is incorrect"}, 401)

    # Update password + invalidate all sessions
    new_hash = hash_password(new_pw)
    conn.execute(
        "UPDATE webclaw_user SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (new_hash, user_id),
    )
    conn.execute("DELETE FROM webclaw_session WHERE user_id = ?", (user_id,))
    conn.commit()

    return {"status": "ok", "message": "Password changed. Please log in again."}


@router.post("/setup")
async def setup(request: Request):
    """Create first admin user. Only works when no users exist."""
    conn = db.get_connection()

    user_count = conn.execute("SELECT COUNT(*) FROM webclaw_user").fetchone()[0]
    if user_count > 0:
        return JSONResponse(
            {"status": "error", "message": "Setup already complete. Use /auth/login."},
            403,
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid request body"}, 400)

    from models import SetupRequest
    from pydantic import ValidationError
    try:
        req = SetupRequest(**body)
    except ValidationError as e:
        return JSONResponse({"status": "error", "message": str(e.errors()[0]["msg"])}, 400)

    email = req.email
    password = req.password
    full_name = req.full_name

    pw_err = validate_password_strength(password)
    if pw_err:
        return JSONResponse({"status": "error", "message": pw_err}, 400)

    user_id = str(uuid.uuid4())
    username = email.split("@")[0]  # derive username from email

    # Create user
    conn.execute(
        """INSERT INTO webclaw_user (id, username, email, full_name, password_hash, status)
           VALUES (?, ?, ?, ?, ?, 'active')""",
        (user_id, username, email, full_name or username, hash_password(password)),
    )

    # Ensure System Manager role exists
    role = conn.execute("SELECT id FROM webclaw_role WHERE name = 'System Manager'").fetchone()
    if not role:
        role_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO webclaw_role (id, name, description, is_system) VALUES (?, 'System Manager', 'Full system access', 1)",
            (role_id,),
        )
    else:
        role_id = role[0] if isinstance(role, tuple) else role["id"]

    # Assign System Manager role
    conn.execute(
        "INSERT INTO webclaw_user_role (id, user_id, role_id) VALUES (?, ?, ?)",
        (str(uuid.uuid4()), user_id, role_id),
    )

    # Create tokens
    secret = get_signing_secret(conn)
    access_token = issue_access_token(conn, user_id, email)
    raw_refresh, refresh_hash = create_refresh_token(user_id, secret)

    ip, ua = _get_client_info(request)
    conn.execute(
        """INSERT INTO webclaw_session (id, user_id, refresh_token_hash, expires_at, ip_address, user_agent)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()),
            user_id,
            refresh_hash,
            (datetime.now(timezone.utc) + REFRESH_TOKEN_EXPIRY).isoformat(),
            ip,
            ua,
        ),
    )
    conn.commit()

    response = JSONResponse({
        "status": "ok",
        "access_token": access_token,
        "user": {
            "user_id": user_id,
            "email": email,
            "full_name": full_name or username,
            "tenant_id": None,
            "is_superadmin": True,
        },
        "message": "Admin account created. You are now logged in.",
    })
    _set_refresh_cookie(response, raw_refresh)
    return response


@router.get("/check-setup")
async def check_setup():
    """Check if initial setup is needed (no users exist)."""
    conn = db.get_connection()
    user_count = conn.execute("SELECT COUNT(*) FROM webclaw_user").fetchone()[0]
    return {"status": "ok", "needs_setup": user_count == 0}


_REGISTER_ATTEMPTS: dict[str, list[float]] = {}
_REGISTER_WINDOW_SEC = 3600
_REGISTER_MAX_PER_WINDOW = 5


def _register_rate_limited(ip: str) -> bool:
    import time
    now = time.time()
    hits = [t for t in _REGISTER_ATTEMPTS.get(ip, []) if now - t < _REGISTER_WINDOW_SEC]
    if len(hits) >= _REGISTER_MAX_PER_WINDOW:
        _REGISTER_ATTEMPTS[ip] = hits
        return True
    hits.append(now)
    _REGISTER_ATTEMPTS[ip] = hits
    return False


@router.get("/plans")
async def public_plans():
    """Public catalog of active subscription plans for the registration wizard."""
    from admin_platform import (
        FEATURE_CATALOG,
        _plan_to_dict,
        ensure_subscription_plans,
    )

    conn = db.get_connection()
    ensure_subscription_plans(conn)
    rows = conn.execute(
        """SELECT * FROM webclaw_subscription_plan
           WHERE is_active = 1
           ORDER BY sort_order, name"""
    ).fetchall()
    return {
        "status": "ok",
        "plans": [_plan_to_dict(r) for r in rows],
        "feature_catalog": FEATURE_CATALOG,
    }


@router.post("/register-hotel")
async def register_hotel(request: Request):
    """Self-service hotel onboarding. Public. Creates tenant + owner session."""
    ip, ua = _get_client_info(request)
    if _register_rate_limited(ip):
        return JSONResponse(
            {"status": "error", "message": "Too many registration attempts. Try again later."},
            429,
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid request body"}, 400)

    from models import RegisterHotelRequest
    from pydantic import ValidationError
    try:
        req = RegisterHotelRequest(**body)
    except ValidationError as e:
        return JSONResponse({"status": "error", "message": str(e.errors()[0]["msg"])}, 400)

    from .onboarding import register_hotel as _register
    result = await _register(
        hotel_name=req.hotel_name,
        admin_email=req.admin_email,
        password=req.password,
        currency=req.currency,
        country=req.country,
        timezone_name=req.timezone,
        subscription_tier=req.subscription_tier,
        ip=ip,
        user_agent=ua,
    )
    http_status = result.pop("http_status", 200)
    refresh = result.pop("refresh_token", None)

    # When platform Stripe is configured, start Checkout for the selected plan.
    if http_status == 200 and result.get("status") == "ok":
        try:
            import stripe_billing as sb
            from admin_platform import _record_subscription_event, ensure_billing_tables

            conn = db.get_connection()
            ensure_billing_tables(conn)
            tenant_meta = result.get("tenant") or {}
            tenant_id = tenant_meta.get("tenant_id")
            plan_code = tenant_meta.get("plan_code") or "starter"
            if tenant_id and sb.platform_stripe_configured(conn):
                origin = os.environ.get("WEBCLAW_CORS_ORIGIN", "http://localhost:3000")
                checkout = sb.create_subscription_checkout(
                    tenant_id=tenant_id,
                    email=req.admin_email,
                    hotel_name=req.hotel_name,
                    plan_code=plan_code,
                    billing_cycle="monthly",
                    success_url=f"{origin}/app/hotel/dashboard?billing=success",
                    cancel_url=f"{origin}/app/hotel/settings/payments?billing=cancelled",
                )
                result["checkout_url"] = checkout.get("checkout_url")
                result["checkout_session_id"] = checkout.get("session_id")
                result["checkout_required"] = True
                _record_subscription_event(
                    conn,
                    tenant_id,
                    "stripe_checkout_started",
                    detail=f"plan={plan_code}; session={checkout.get('session_id')}",
                    actor_email=req.admin_email,
                )
            else:
                result["checkout_required"] = False
        except Exception:
            # Registration itself succeeded — billing can be completed later.
            result["checkout_required"] = False
            result["checkout_error"] = "Could not start Stripe Checkout; complete billing from Hotel settings."

    response = JSONResponse(result, status_code=http_status)
    if http_status == 200 and refresh:
        _set_refresh_cookie(response, refresh)
    return response


# ── Enterprise SSO (OIDC / SAML ACS) ──────────────────────────────────────────

def _ensure_sso_state_table(conn) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS webclaw_sso_state (
            state       TEXT PRIMARY KEY,
            tenant_id   TEXT NOT NULL,
            nonce       TEXT,
            redirect_uri TEXT,
            created_at  TEXT NOT NULL,
            expires_at  TEXT NOT NULL
        )"""
    )


def _sso_callback_base(request: Request) -> str:
    explicit = os.environ.get("WEBCLAW_SSO_CALLBACK_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    origin = os.environ.get("WEBCLAW_API_PUBLIC_URL", "").strip().rstrip("/")
    if origin:
        return f"{origin}/api/v1/auth/sso/callback"
    # Derive from request
    return str(request.url.replace(path="/api/v1/auth/sso/callback", query=""))


def _sso_test_mode(issuer_url: str | None) -> bool:
    if os.environ.get("WEBCLAW_SSO_TEST_MODE", "").lower() in ("1", "true", "yes"):
        return True
    issuer = (issuer_url or "").strip().lower()
    return issuer.startswith("test://") or issuer == "test"


def _load_identity(conn, tenant_id: str):
    from admin_growth import ensure_growth_tables, _row

    ensure_growth_tables(conn)
    r = conn.execute(
        "SELECT * FROM webclaw_tenant_identity WHERE tenant_id = ?", (tenant_id,)
    ).fetchone()
    if not r:
        return None
    return {
        "tenant_id": tenant_id,
        "sso_enabled": bool(_row(r, "sso_enabled", 1)),
        "sso_protocol": (_row(r, "sso_protocol", 2) or "oidc"),
        "issuer_url": _row(r, "issuer_url", 3) or "",
        "client_id": _row(r, "client_id", 4) or "",
        "client_secret": _row(r, "client_secret", 5) or "",
        "metadata_url": _row(r, "metadata_url", 6) or "",
    }


async def _oidc_discovery(issuer_url: str, metadata_url: str | None) -> dict:
    import httpx

    url = (metadata_url or "").strip() or f"{issuer_url.rstrip('/')}/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


def _issue_session_for_user(conn, user_id: str, user_email: str, request: Request) -> tuple[str, str]:
    """Create access + refresh tokens and session row. Returns (access, refresh)."""
    ip, ua = _get_client_info(request)
    secret = get_signing_secret(conn)
    access_token = issue_access_token(conn, user_id, user_email)
    raw_refresh, refresh_hash = create_refresh_token(user_id, secret)
    conn.execute(
        """INSERT INTO webclaw_session (id, user_id, refresh_token_hash, expires_at, ip_address, user_agent)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()),
            user_id,
            refresh_hash,
            (datetime.now(timezone.utc) + REFRESH_TOKEN_EXPIRY).isoformat(),
            ip,
            ua,
        ),
    )
    conn.execute(
        "UPDATE webclaw_user SET last_login = ?, failed_login_attempts = 0, locked_until = NULL WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), user_id),
    )
    sessions = conn.execute(
        "SELECT id FROM webclaw_session WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()
    if len(sessions) > MAX_SESSIONS_PER_USER:
        for s in sessions[MAX_SESSIONS_PER_USER:]:
            sid = s[0] if isinstance(s, tuple) else s["id"]
            conn.execute("DELETE FROM webclaw_session WHERE id = ?", (sid,))
    conn.commit()
    return access_token, raw_refresh


def _resolve_or_provision_sso_user(conn, tenant_id: str, email: str, full_name: str = "") -> str | None:
    """Return user_id for email if they belong to tenant; JIT-provision when missing."""
    from db_access import table_columns
    from auth.passwords import hash_password

    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return None
    user = conn.execute(
        "SELECT id, status, tenant_id FROM webclaw_user WHERE email = ?", (email,)
    ).fetchone()
    cols = set(table_columns(conn, "webclaw_user") or [])
    if user:
        user_id = user[0] if isinstance(user, tuple) else user["id"]
        status = user[1] if isinstance(user, tuple) else user["status"]
        if status != "active":
            return None
        # Membership check
        ut_cols = set(table_columns(conn, "webclaw_user_tenant") or [])
        if ut_cols:
            link = conn.execute(
                "SELECT 1 FROM webclaw_user_tenant WHERE user_id = ? AND tenant_id = ? LIMIT 1",
                (user_id, tenant_id),
            ).fetchone()
            if link:
                return user_id
        if "tenant_id" in cols:
            tid = user[2] if isinstance(user, tuple) else user.get("tenant_id")
            if str(tid or "") == str(tenant_id):
                return user_id
        # Platform operators without tenant link may still SSO if email matches
        return user_id

    # JIT provision
    user_id = str(uuid.uuid4())
    name = full_name or email.split("@")[0]
    temp_pw = uuid.uuid4().hex[:12] + "Aa1!"
    if "tenant_id" in cols:
        conn.execute(
            """INSERT INTO webclaw_user
               (id, username, email, full_name, password_hash, status, created_at, tenant_id)
               VALUES (?, ?, ?, ?, ?, 'active', ?, ?)""",
            (
                user_id,
                email.split("@")[0][:64],
                email,
                name,
                hash_password(temp_pw),
                datetime.now(timezone.utc).isoformat(),
                tenant_id,
            ),
        )
    else:
        conn.execute(
            """INSERT INTO webclaw_user
               (id, username, email, full_name, password_hash, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'active', ?)""",
            (
                user_id,
                email.split("@")[0][:64],
                email,
                name,
                hash_password(temp_pw),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    try:
        from tenant import upsert_membership

        upsert_membership(conn, user_id, tenant_id, role_code="staff", role_name="Staff")
    except Exception:
        pass
    conn.commit()
    return user_id


@router.get("/sso/{tenant_id}/start")
async def sso_start(tenant_id: str, request: Request, redirect: str | None = None):
    """Begin OIDC authorize redirect (or SAML IdP redirect). Returns JSON with authorize_url."""
    from fastapi.responses import RedirectResponse
    from urllib.parse import urlencode

    conn = db.get_connection()
    _ensure_sso_state_table(conn)
    ident = _load_identity(conn, tenant_id)
    if not ident or not ident["sso_enabled"]:
        return JSONResponse(
            {"status": "error", "message": "SSO is not enabled for this tenant"}, 404
        )

    state = uuid.uuid4().hex
    nonce = uuid.uuid4().hex
    callback = _sso_callback_base(request)
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(minutes=10)).isoformat()
    conn.execute(
        """INSERT INTO webclaw_sso_state (state, tenant_id, nonce, redirect_uri, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (state, tenant_id, nonce, redirect or "", now.isoformat(), expires),
    )
    conn.commit()

    protocol = (ident["sso_protocol"] or "oidc").lower()
    want_redirect = (request.query_params.get("format") or "").lower() == "redirect"

    if _sso_test_mode(ident.get("issuer_url")):
        # Loopback authorize URL for automated tests / local IdP stubs
        qs = urlencode({"code": "sso_test", "state": state})
        authorize_url = f"{callback}?{qs}"
        if want_redirect:
            return RedirectResponse(authorize_url, status_code=302)
        return {
            "status": "ok",
            "authorize_url": authorize_url,
            "state": state,
            "protocol": protocol,
            "test_mode": True,
        }

    if protocol == "saml":
        # Browser posts to ACS; return IdP SSO URL from metadata_url / issuer
        idp = (ident.get("metadata_url") or ident.get("issuer_url") or "").strip()
        if not idp:
            return JSONResponse(
                {"status": "error", "message": "SAML IdP URL (metadata_url/issuer_url) required"},
                400,
            )
        qs = urlencode({"SAMLRequest": "pending", "RelayState": state})
        authorize_url = f"{idp}?{qs}" if "?" not in idp else f"{idp}&{qs}"
        if want_redirect:
            return RedirectResponse(authorize_url, status_code=302)
        return {
            "status": "ok",
            "authorize_url": authorize_url,
            "state": state,
            "protocol": "saml",
            "acs_url": f"/api/v1/auth/sso/{tenant_id}/saml/acs",
        }

    # OIDC
    client_id = ident.get("client_id") or ""
    if not client_id or not ident.get("issuer_url"):
        return JSONResponse(
            {"status": "error", "message": "OIDC issuer_url and client_id required"}, 400
        )
    try:
        meta = await _oidc_discovery(ident["issuer_url"], ident.get("metadata_url"))
        auth_ep = meta.get("authorization_endpoint")
    except Exception as e:
        return JSONResponse(
            {"status": "error", "message": f"OIDC discovery failed: {e}"}, 502
        )
    if not auth_ep:
        return JSONResponse(
            {"status": "error", "message": "No authorization_endpoint in IdP metadata"}, 502
        )
    qs = urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "scope": "openid email profile",
            "redirect_uri": callback,
            "state": state,
            "nonce": nonce,
        }
    )
    authorize_url = f"{auth_ep}?{qs}"
    if want_redirect:
        return RedirectResponse(authorize_url, status_code=302)
    return {
        "status": "ok",
        "authorize_url": authorize_url,
        "state": state,
        "protocol": "oidc",
        "callback_url": callback,
    }


async def _complete_sso_login(
    conn, tenant_id: str, email: str, full_name: str, request: Request, redirect_uri: str
):
    user_id = _resolve_or_provision_sso_user(conn, tenant_id, email, full_name)
    if not user_id:
        return JSONResponse(
            {"status": "error", "message": "SSO user not found or disabled"}, 403
        )
    access, refresh = _issue_session_for_user(conn, user_id, email, request)
    # Prefer browser redirect to frontend with token fragment for SPA
    fe = (
        redirect_uri
        or os.environ.get("WEBCLAW_CORS_ORIGIN", "http://localhost:3000")
    ).rstrip("/")
    # JSON for API clients (Accept: application/json or ?format=json)
    fmt = (request.query_params.get("format") or "").lower()
    accept = (request.headers.get("accept") or "").lower()
    if fmt == "json" or "application/json" in accept:
        resp = JSONResponse(
            {
                "status": "ok",
                "access_token": access,
                "token_type": "bearer",
                "tenant_id": tenant_id,
                "email": email,
            }
        )
        _set_refresh_cookie(resp, refresh)
        return resp
    from fastapi.responses import RedirectResponse
    from urllib.parse import urlencode

    dest = f"{fe}/login?{urlencode({'sso': '1', 'access_token': access})}"
    resp = RedirectResponse(dest, status_code=302)
    _set_refresh_cookie(resp, refresh)
    return resp


@router.get("/sso/callback")
async def sso_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    """OIDC authorization-code callback."""
    if error:
        return JSONResponse({"status": "error", "message": f"IdP error: {error}"}, 400)
    if not code or not state:
        return JSONResponse({"status": "error", "message": "code and state required"}, 400)

    conn = db.get_connection()
    _ensure_sso_state_table(conn)
    row = conn.execute(
        "SELECT tenant_id, nonce, redirect_uri, expires_at FROM webclaw_sso_state WHERE state = ?",
        (state,),
    ).fetchone()
    if not row:
        return JSONResponse({"status": "error", "message": "Invalid or expired SSO state"}, 400)
    tenant_id = row[0] if isinstance(row, tuple) else row["tenant_id"]
    redirect_uri = (row[2] if isinstance(row, tuple) else row["redirect_uri"]) or ""
    expires_at = row[3] if isinstance(row, tuple) else row["expires_at"]
    try:
        if datetime.fromisoformat(expires_at) < datetime.now(timezone.utc):
            conn.execute("DELETE FROM webclaw_sso_state WHERE state = ?", (state,))
            conn.commit()
            return JSONResponse({"status": "error", "message": "SSO state expired"}, 400)
    except Exception:
        pass
    conn.execute("DELETE FROM webclaw_sso_state WHERE state = ?", (state,))
    conn.commit()

    ident = _load_identity(conn, tenant_id)
    if not ident or not ident["sso_enabled"]:
        return JSONResponse({"status": "error", "message": "SSO not enabled"}, 404)

    email = ""
    full_name = ""
    if code == "sso_test" and _sso_test_mode(ident.get("issuer_url")):
        # Dev/test: email from query or default
        email = (request.query_params.get("email") or "").strip().lower()
        if not email:
            email = f"sso-user@{tenant_id[:8]}.test"
        full_name = request.query_params.get("name") or "SSO Test User"
    else:
        # Real OIDC token exchange
        import httpx

        try:
            meta = await _oidc_discovery(ident["issuer_url"], ident.get("metadata_url"))
            token_ep = meta.get("token_endpoint")
            userinfo_ep = meta.get("userinfo_endpoint")
            if not token_ep:
                return JSONResponse({"status": "error", "message": "No token_endpoint"}, 502)
            callback = _sso_callback_base(request)
            async with httpx.AsyncClient(timeout=15.0) as client:
                tok = await client.post(
                    token_ep,
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": callback,
                        "client_id": ident["client_id"],
                        "client_secret": ident.get("client_secret") or "",
                    },
                    headers={"Accept": "application/json"},
                )
                if tok.status_code >= 400:
                    return JSONResponse(
                        {
                            "status": "error",
                            "message": f"Token exchange failed: {tok.text[:300]}",
                        },
                        502,
                    )
                tokens = tok.json()
                access = tokens.get("access_token")
                if userinfo_ep and access:
                    ui = await client.get(
                        userinfo_ep,
                        headers={"Authorization": f"Bearer {access}"},
                    )
                    if ui.status_code < 400:
                        info = ui.json()
                        email = (
                            info.get("email")
                            or info.get("preferred_username")
                            or ""
                        ).lower()
                        full_name = info.get("name") or info.get("given_name") or ""
                if not email and tokens.get("id_token"):
                    # Best-effort decode JWT payload (no signature verify here — IdP TLS + code exchange)
                    import base64
                    import json as _json

                    parts = str(tokens["id_token"]).split(".")
                    if len(parts) >= 2:
                        pad = "=" * (-len(parts[1]) % 4)
                        payload = _json.loads(base64.urlsafe_b64decode(parts[1] + pad))
                        email = (payload.get("email") or payload.get("preferred_username") or "").lower()
                        full_name = payload.get("name") or full_name
        except Exception as e:
            return JSONResponse(
                {"status": "error", "message": f"OIDC callback failed: {e}"}, 502
            )

    if not email:
        return JSONResponse(
            {"status": "error", "message": "IdP did not return an email claim"}, 400
        )
    return await _complete_sso_login(conn, tenant_id, email, full_name, request, redirect_uri)


@router.post("/sso/{tenant_id}/saml/acs")
async def sso_saml_acs(tenant_id: str, request: Request):
    """SAML Assertion Consumer Service — accepts form POST or JSON test payload."""
    import base64
    import json as _json
    from urllib.parse import parse_qs

    conn = db.get_connection()
    _ensure_sso_state_table(conn)
    ident = _load_identity(conn, tenant_id)
    if not ident or not ident["sso_enabled"]:
        return JSONResponse({"status": "error", "message": "SSO not enabled"}, 404)
    if (ident.get("sso_protocol") or "").lower() != "saml" and not _sso_test_mode(
        ident.get("issuer_url")
    ):
        return JSONResponse({"status": "error", "message": "Tenant is not configured for SAML"}, 400)

    content_type = (request.headers.get("content-type") or "").lower()
    relay_state = ""
    email = ""
    full_name = ""
    if "application/json" in content_type:
        body = await request.json()
        email = str(body.get("email") or "").strip().lower()
        full_name = str(body.get("name") or "")
        relay_state = str(body.get("RelayState") or body.get("state") or "")
    else:
        raw = (await request.body()).decode("utf-8", errors="replace")
        form = parse_qs(raw)
        relay_state = (form.get("RelayState") or [""])[0]
        saml_b64 = (form.get("SAMLResponse") or [""])[0]
        if saml_b64 and _sso_test_mode(ident.get("issuer_url")):
            try:
                decoded = base64.b64decode(saml_b64 + "=" * (-len(saml_b64) % 4)).decode(
                    "utf-8", errors="replace"
                )
                if decoded.strip().startswith("{"):
                    data = _json.loads(decoded)
                    email = str(data.get("email") or "").lower()
                    full_name = str(data.get("name") or "")
                else:
                    # Minimal XML email extraction
                    import re as _re

                    m = _re.search(
                        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", decoded
                    )
                    if m:
                        email = m.group(0).lower()
            except Exception:
                pass
        elif saml_b64:
            return JSONResponse(
                {
                    "status": "error",
                    "message": "Full SAML XML signature validation requires IdP metadata; use test mode or OIDC",
                },
                501,
            )

    # Validate relay state if present
    redirect_uri = ""
    if relay_state:
        row = conn.execute(
            "SELECT redirect_uri, expires_at FROM webclaw_sso_state WHERE state = ? AND tenant_id = ?",
            (relay_state, tenant_id),
        ).fetchone()
        if row:
            redirect_uri = (row[0] if isinstance(row, tuple) else row["redirect_uri"]) or ""
            conn.execute("DELETE FROM webclaw_sso_state WHERE state = ?", (relay_state,))
            conn.commit()

    if not email:
        return JSONResponse(
            {"status": "error", "message": "Could not extract email from SAMLResponse"}, 400
        )
    return await _complete_sso_login(conn, tenant_id, email, full_name, request, redirect_uri)
