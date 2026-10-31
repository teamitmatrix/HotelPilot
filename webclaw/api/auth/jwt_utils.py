"""JWT token creation and verification (PyJWT, HS256)."""
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from db_access import DbConn

ACCESS_TOKEN_EXPIRY = timedelta(minutes=15)
REFRESH_TOKEN_EXPIRY = timedelta(days=7)
ALGORITHM = "HS256"


def get_signing_secret(conn: DbConn) -> str:
    """Get or auto-generate the JWT signing secret from the config table."""
    row = conn.execute("SELECT value FROM webclaw_config WHERE key = 'jwt_secret'").fetchone()
    if row:
        return row[0] if isinstance(row, tuple) else row["value"]
    # First time: generate and store
    secret = secrets.token_hex(32)  # 256-bit
    conn.execute(
        "INSERT INTO webclaw_config (key, value) VALUES ('jwt_secret', ?)", (secret,)
    )
    conn.commit()
    return secret


# Impersonation sessions are intentionally shorter than normal access tokens.
IMPERSONATION_TOKEN_EXPIRY = timedelta(minutes=15)


def create_access_token(
    user_id: str,
    email: str,
    secret: str,
    *,
    tenant_id: str | None = None,
    is_superadmin: bool = False,
    impersonate_tenant_id: str | None = None,
    impersonation_reason: str | None = None,
    expires_delta: timedelta | None = None,
    group_id: str | None = None,
    assigned_properties: list[str] | tuple[str, ...] | None = None,
    active_property_id: str | None = None,
) -> str:
    """Create a short-lived access token (15 min).

    ``tenant_id`` / ``property_id`` is the hotel's company UUID. SuperAdmins have
    tenant_id=None and ``is_superadmin=True`` so middleware can skip row isolation.
    Impersonation tokens carry a reason and use ``IMPERSONATION_TOKEN_EXPIRY``
    unless ``expires_delta`` is provided.
    """
    now = datetime.now(timezone.utc)
    impersonating = bool(impersonate_tenant_id)
    property_id = impersonate_tenant_id or active_property_id or tenant_id
    props = list(assigned_properties or ([] if not property_id else [property_id]))
    if expires_delta is not None:
        lifetime = expires_delta
    elif impersonating:
        lifetime = IMPERSONATION_TOKEN_EXPIRY
    else:
        lifetime = ACCESS_TOKEN_EXPIRY
    payload = {
        "sub": user_id,
        "email": email,
        "type": "access",
        "iat": now,
        "exp": now + lifetime,
        "jti": str(uuid.uuid4()),
        "tenant_id": impersonate_tenant_id or tenant_id,
        "property_id": property_id,
        "group_id": group_id,
        "assigned_properties": props,
        "active_property_id": property_id,
        "is_superadmin": bool(is_superadmin) and not impersonating,
        "is_impersonating": impersonating,
    }
    if impersonate_tenant_id:
        payload["impersonate_tenant_id"] = impersonate_tenant_id
    if impersonating and impersonation_reason:
        payload["impersonation_reason"] = impersonation_reason[:500]
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def create_refresh_token(user_id: str, secret: str) -> tuple[str, str]:
    """Create a refresh token (7 day). Returns (raw_token, token_hash)."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "type": "refresh",
        "iat": now,
        "exp": now + REFRESH_TOKEN_EXPIRY,
        "jti": str(uuid.uuid4()),
    }
    raw_token = jwt.encode(payload, secret, algorithm=ALGORITHM)
    token_hash = hash_token(raw_token)
    return raw_token, token_hash


def verify_token(token: str, secret: str, expected_type: str = "access") -> dict | None:
    """Verify and decode a JWT token. Returns payload or None."""
    try:
        payload = jwt.decode(token, secret, algorithms=[ALGORITHM])
        if payload.get("type") != expected_type:
            return None
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def hash_token(token: str) -> str:
    """SHA-256 hash of a token for DB storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def issue_access_token(conn: DbConn, user_id: str, email: str) -> str:
    """Create an access token stamped with the user's live tenant + role."""
    from tenant import build_context

    ctx = build_context(conn, user_id, email)
    secret = get_signing_secret(conn)
    return create_access_token(
        user_id,
        email,
        secret,
        tenant_id=ctx.tenant_id,
        is_superadmin=ctx.is_superadmin,
        group_id=ctx.group_id,
        assigned_properties=ctx.assigned_properties,
        active_property_id=ctx.active_property_id or ctx.tenant_id,
    )
