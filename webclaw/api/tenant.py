"""Request-scoped tenant context for the WebClaw API.

Hotel staff are bound to one active ``tenant_id`` (the ERP ``company.id`` UUID).
Staff assigned to several properties keep a home tenant and a membership list;
the active property arrives on the ``X-Tenant-Id`` header.
Platform SuperAdmins (System Manager with a NULL tenant) may act across
hotels by passing an explicit company-id, or by impersonating one hotel.
"""
from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass

from db_access import DbConn, is_missing_relation

_ctx: ContextVar["TenantContext | None"] = ContextVar("webclaw_tenant", default=None)

SUPERADMIN_ROLE = "System Manager"
HOTEL_OWNER_ROLE = "Hotel Owner"
HOTEL_ADMIN_ROLE = "Hotel Admin"
GENERAL_MANAGER_ROLE = "General Manager"
HOTEL_MANAGER_ROLE = "Hotel Manager"
FRONT_DESK_ROLE = "Front Desk"
HOUSEKEEPING_ROLE = "Housekeeping"
HOTEL_ACCOUNTANT_ROLE = "Hotel Accountant"
ACCOUNTANT_ROLE = "Branch Accountant"
HOTEL_STAFF_ROLE = "Hotel Staff"

TENANT_PARAM_KEYS = (
    "tenant_id", "tenant-id",
    "company_id", "company-id",
    # Note: hotel_id / hotel-id are hospitalityclaw entity FKs (hospitalityclaw_hotel.id),
    # not the ERP company/tenant UUID. Do not treat them as tenant keys.
)

# Skill actions that are platform-level (no hotel tenant required).
PLATFORM_SKILL_ACTIONS = frozenset({
    ("erpclaw", "initialize-database"),
    ("erpclaw", "check-installation"),
    ("erpclaw", "install-guide"),
    ("erpclaw", "setup-company"),
    ("erpclaw", "list-companies"),
    ("erpclaw", "status"),
    ("webclaw", "status"),
    ("webclaw", "list-users"),
    ("hospitalityclaw", "hospitality-initialize-schema"),
})


@dataclass(frozen=True)
class TenantContext:
    user_id: str
    email: str | None
    tenant_id: str | None
    is_superadmin: bool
    roles: tuple[str, ...]
    impersonating: bool = False
    impersonation_reason: str | None = None
    # Group → Property hierarchy (property_id == tenant_id / company.id).
    group_id: str | None = None
    assigned_properties: tuple[str, ...] = ()
    active_property_id: str | None = None  # UUID or "ALL"
    scope_all_properties: bool = False

    @property
    def property_id(self) -> str | None:
        """Active property UUID when scoped to one branch; None when ALL/superadmin."""
        if self.scope_all_properties:
            return None
        return self.active_property_id or self.tenant_id

    @property
    def isolation_id(self) -> str | None:
        """UUID that must appear on every hotel-scoped row. None = SuperAdmin or ALL."""
        if self.is_superadmin and not self.impersonating:
            return None
        if self.scope_all_properties:
            return None
        return self.property_id or self.tenant_id


def get_tenant_context() -> TenantContext | None:
    return _ctx.get()


def set_tenant_context(ctx: TenantContext | None):
    return _ctx.set(ctx)


def reset_tenant_context(token) -> None:
    _ctx.reset(token)


def is_superadmin_user(conn: DbConn, user_id: str) -> bool:
    from rbac import _is_superadmin
    return _is_superadmin(conn, user_id)


def load_user_roles(conn: DbConn, user_id: str) -> list[str]:
    rows = conn.execute(
        """SELECT r.name FROM webclaw_user_role ur
           JOIN webclaw_role r ON r.id = ur.role_id
           WHERE ur.user_id = ?""",
        (user_id,),
    ).fetchall()
    return [r[0] if isinstance(r, tuple) else r["name"] for r in rows]


def load_user_tenant_id(conn: DbConn, user_id: str) -> str | None:
    """Read webclaw_user.tenant_id, tolerant of pre-migration schemas."""
    try:
        row = conn.execute(
            "SELECT tenant_id FROM webclaw_user WHERE id = ?", (user_id,)
        ).fetchone()
    except Exception as exc:
        if is_missing_relation(exc):
            return None
        raise
    if not row:
        return None
    value = row[0] if isinstance(row, tuple) else row["tenant_id"]
    return value or None


def build_context(conn: DbConn, user_id: str, email: str | None = None) -> TenantContext:
    from property_scope import (
        ensure_group_schema_webclaw,
        ensure_owner_group_for_user,
        user_group_id,
    )

    roles = tuple(load_user_roles(conn, user_id))
    superadmin = SUPERADMIN_ROLE in roles
    tenant_id = load_user_tenant_id(conn, user_id)
    ensure_group_schema_webclaw(conn)
    group_id = user_group_id(conn, user_id)
    # Lazy-provision Group→Property for legacy single-hotel owners so the
    # property switcher and branch APIs light up without a manual migration.
    if not superadmin and tenant_id and not group_id:
        try:
            group_id = ensure_owner_group_for_user(conn, user_id, tenant_id)
        except Exception:
            group_id = user_group_id(conn, user_id)
    memberships = load_memberships(conn, user_id)
    assigned = tuple(
        sorted({str(m["tenant_id"]) for m in memberships if m.get("tenant_id")})
    )
    if tenant_id and tenant_id not in assigned:
        assigned = tuple(sorted({*assigned, tenant_id}))
    return TenantContext(
        user_id=user_id,
        email=email,
        tenant_id=tenant_id,
        is_superadmin=superadmin,
        roles=roles,
        group_id=group_id,
        assigned_properties=assigned,
        active_property_id=tenant_id,
        scope_all_properties=False,
    )


def apply_impersonation(ctx: TenantContext, payload: dict | None) -> TenantContext:
    """Scope a SuperAdmin session to a hotel when the JWT carries impersonation."""
    if not ctx.is_superadmin or not payload:
        return ctx
    tid = payload.get("impersonate_tenant_id")
    if not tid:
        return ctx
    reason = payload.get("impersonation_reason")
    return TenantContext(
        user_id=ctx.user_id,
        email=ctx.email,
        tenant_id=str(tid),
        is_superadmin=False,
        roles=ctx.roles,
        impersonating=True,
        impersonation_reason=str(reason)[:500] if reason else None,
        group_id=ctx.group_id,
        assigned_properties=(str(tid),),
        active_property_id=str(tid),
        scope_all_properties=False,
    )


def payload_tenant_id(params: dict) -> str | None:
    for key in TENANT_PARAM_KEYS:
        value = params.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


class TenantIsolationError(Exception):
    def __init__(self, message: str, status_code: int = 403):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def enforce_payload_tenant(ctx: TenantContext, params: dict) -> dict:
    """Bind / reject tenant keys on a skill-action payload.

    Hotel users: every tenant/company id is overwritten with their
    session tenant. A mismatched payload is rejected, not silently rewritten,
    so a confused or hostile client cannot probe other hotels.
    Entity keys such as hospitality hotel_id are left intact; ownership is
    enforced inside the skill against the bound tenant_id.

    Group owners with ``active_property_id=ALL`` may only run read/list/status
    actions; writes must target a specific property.

    SuperAdmins: payload tenant is allowed through as company-id; missing
    tenant is allowed only for platform actions (caller decides).
    """
    claimed = payload_tenant_id(params)
    isolation = ctx.isolation_id

    if ctx.scope_all_properties:
        action = str(params.get("_action") or params.get("action") or "")
        # Router should pass action separately; also inspect common names.
        # Conservative: block mutating verbs when aggregating.
        mutating = any(
            action.startswith(p)
            for p in (
                "add-", "create-", "update-", "delete-", "check-in", "check-out",
                "assign-", "cancel-", "settle-", "post-", "seed-", "setup-",
            )
        )
        if mutating:
            raise TenantIsolationError(
                "Select a specific property before changing data "
                "(X-Property-ID=ALL is read-only)",
                status_code=400,
            )
        # Group scope is applied via ERPCLAW_GROUP_ID / ERPCLAW_TENANT_IDS in
        # execute_skill — never as CLI flags (skills reject unknown --group-id).
        return {k: v for k, v in params.items() if k not in TENANT_PARAM_KEYS}

    if isolation:
        if claimed and claimed != isolation:
            raise TenantIsolationError(
                "tenant_id mismatch: hotel staff cannot act on another hotel"
            )
        bound = {k: v for k, v in params.items() if k not in TENANT_PARAM_KEYS}
        bound["company-id"] = isolation
        bound["tenant-id"] = isolation
        return bound

    # SuperAdmin — do not auto-pick the first company (that was a data leak).
    return params


def require_tenant_for_action(ctx: TenantContext, skill: str, action: str) -> None:
    """Hotel-scoped actions must have a tenant; SuperAdmin platform ops may not."""
    if ctx.isolation_id:
        return
    if ctx.scope_all_properties:
        # Read aggregation is allowed; mutating guarded in enforce_payload_tenant.
        return
    if ctx.is_superadmin:
        return
    if (skill, action) in PLATFORM_SKILL_ACTIONS:
        return
    raise TenantIsolationError(
        "tenant_id is required; this account is not bound to a hotel",
        status_code=403,
    )


# Client headers that select the active hotel / branch for this request.
TENANT_HEADER = "x-tenant-id"
PROPERTY_HEADER = "x-property-id"
PROPERTY_ALL = "ALL"

_NAME_TO_CODE = {
    "System Manager": "SUPER_ADMIN",
    "SUPER_ADMIN": "SUPER_ADMIN",
    "Hotel Owner": "HOTEL_OWNER",
    "HOTEL_OWNER": "HOTEL_OWNER",
    "Hotel Admin": "HOTEL_ADMIN",
    "HOTEL_ADMIN": "HOTEL_ADMIN",
    "Hotel Manager": "HOTEL_MANAGER",
    "HOTEL_MANAGER": "HOTEL_MANAGER",
    "General Manager": "GENERAL_MANAGER",
    "GENERAL_MANAGER": "GENERAL_MANAGER",
    "Front Desk": "FRONT_DESK",
    "FRONT_DESK": "FRONT_DESK",
    "Housekeeping": "HOUSEKEEPING",
    "HOUSEKEEPING": "HOUSEKEEPING",
    "Accountant": "ACCOUNTANT",
    "Branch Accountant": "ACCOUNTANT",
    "ACCOUNTANT": "ACCOUNTANT",
    "Hotel Accountant": "HOTEL_ACCOUNTANT",
    "HOTEL_ACCOUNTANT": "HOTEL_ACCOUNTANT",
    "Hotel Staff": "HOTEL_STAFF",
    "Regional Manager": "REGIONAL_MANAGER",
}


def role_code_from_name(name: str | None, code: str | None = None) -> str:
    """Map a stored role name or code onto the stable API role code."""
    if code and str(code).strip():
        return str(code).strip()
    if not name:
        return ""
    return _NAME_TO_CODE.get(str(name).strip(), str(name).strip())


def ensure_user_tenant_table(conn: DbConn) -> None:
    """Membership of a user in one or more hotel tenants."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS webclaw_user_tenant (
            id          TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL,
            tenant_id   TEXT NOT NULL,
            role_code   TEXT,
            role_name   TEXT,
            hotel_name  TEXT,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, tenant_id)
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_webclaw_user_tenant_user ON webclaw_user_tenant(user_id)"
    )


def upsert_membership(
    conn: DbConn,
    user_id: str,
    tenant_id: str | None,
    *,
    role_code: str | None = None,
    role_name: str | None = None,
    hotel_name: str | None = None,
) -> None:
    """Grant (or refresh) one hotel membership. No-op when tenant_id is empty."""
    if not user_id or not tenant_id:
        return
    ensure_user_tenant_table(conn)
    row = conn.execute(
        "SELECT id FROM webclaw_user_tenant WHERE user_id = ? AND tenant_id = ?",
        (user_id, tenant_id),
    ).fetchone()
    if row:
        conn.execute(
            """UPDATE webclaw_user_tenant
               SET role_code = COALESCE(?, role_code),
                   role_name = COALESCE(?, role_name),
                   hotel_name = COALESCE(?, hotel_name)
               WHERE user_id = ? AND tenant_id = ?""",
            (role_code, role_name, hotel_name, user_id, tenant_id),
        )
        return
    conn.execute(
        """INSERT INTO webclaw_user_tenant
           (id, user_id, tenant_id, role_code, role_name, hotel_name)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), user_id, tenant_id, role_code, role_name, hotel_name),
    )


def _row_get(row, key: str, index: int):
    if row is None:
        return None
    if isinstance(row, tuple):
        return row[index]
    return row[key]


def _remember(by_tenant: dict, tenant_id, role_code=None, role_name=None, hotel_name=None) -> None:
    if not tenant_id:
        return
    tid = str(tenant_id)
    code = role_code_from_name(role_name, role_code)
    current = by_tenant.get(tid)
    if current is None:
        by_tenant[tid] = {
            "tenant_id": tid,
            "role": code,
            "role_name": role_name or "",
            "hotel_name": hotel_name or "",
        }
        return
    if code and not current["role"]:
        current["role"] = code
    if role_name and not current["role_name"]:
        current["role_name"] = role_name
    if hotel_name and not current["hotel_name"]:
        current["hotel_name"] = hotel_name


def _fill_hotel_names(by_tenant: dict) -> None:
    missing = [tid for tid, item in by_tenant.items() if not item.get("hotel_name")]
    if not missing:
        return
    names: dict[str, str] = {}
    try:
        import db as dbmod

        placeholders = ",".join("?" for _ in missing)
        sql = f"""SELECT id, company_id, hotel_name FROM pms_tenant
                  WHERE id IN ({placeholders}) OR company_id IN ({placeholders})"""
        params = [*missing, *missing]

        raw = dbmod.get_skill_db("erpclaw")
        close_raw = True
        if raw is not None:
            try:
                rows = raw.execute(sql, params).fetchall()
                for row in rows:
                    label = row["hotel_name"] if not isinstance(row, tuple) else row[2]
                    if not label:
                        continue
                    rid = row["id"] if not isinstance(row, tuple) else row[0]
                    cid = row["company_id"] if not isinstance(row, tuple) else row[1]
                    if rid:
                        names[str(rid)] = label
                    if cid:
                        names[str(cid)] = label
            finally:
                if close_raw:
                    raw.close()
    except Exception:
        names = {}
    for tid, item in by_tenant.items():
        if not item["hotel_name"]:
            item["hotel_name"] = names.get(tid) or "Hotel"


def load_memberships(conn: DbConn, user_id: str) -> list[dict]:
    """Hotels this user may activate, including their home tenant.

    Sources, in order: ``webclaw_user_tenant``, role rows that name a tenant,
    and ``webclaw_user.tenant_id``. A role with a NULL tenant applies to the
    home hotel only — extra properties must be explicit memberships.
    """
    by_tenant: dict[str, dict] = {}
    home = load_user_tenant_id(conn, user_id)

    try:
        rows = conn.execute(
            """SELECT tenant_id, role_code, role_name, hotel_name
               FROM webclaw_user_tenant WHERE user_id = ?""",
            (user_id,),
        ).fetchall()
    except Exception as exc:
        if not is_missing_relation(exc):
            raise
        try:
            ensure_user_tenant_table(conn)
            rows = conn.execute(
                """SELECT tenant_id, role_code, role_name, hotel_name
                   FROM webclaw_user_tenant WHERE user_id = ?""",
                (user_id,),
            ).fetchall()
        except Exception as exc2:
            if not is_missing_relation(exc2):
                raise
            rows = []
    for row in rows:
        _remember(
            by_tenant,
            _row_get(row, "tenant_id", 0),
            _row_get(row, "role_code", 1),
            _row_get(row, "role_name", 2),
            _row_get(row, "hotel_name", 3),
        )

    try:
        role_rows = conn.execute(
            """SELECT ur.tenant_id AS tenant_id, r.name AS role_name, r.code AS role_code
               FROM webclaw_user_role ur
               JOIN webclaw_role r ON r.id = ur.role_id
               WHERE ur.user_id = ?""",
            (user_id,),
        ).fetchall()
        for row in role_rows:
            tid = _row_get(row, "tenant_id", 0) or home
            _remember(
                by_tenant,
                tid,
                _row_get(row, "role_code", 2),
                _row_get(row, "role_name", 1),
                None,
            )
    except Exception as exc:
        if not is_missing_relation(exc):
            raise
        try:
            role_rows = conn.execute(
                """SELECT r.name AS role_name
                   FROM webclaw_user_role ur
                   JOIN webclaw_role r ON r.id = ur.role_id
                   WHERE ur.user_id = ?""",
                (user_id,),
            ).fetchall()
            for row in role_rows:
                _remember(by_tenant, home, None, _row_get(row, "role_name", 0), None)
        except Exception as exc2:
            if not is_missing_relation(exc2):
                raise

    if home and home not in by_tenant:
        _remember(by_tenant, home, None, None, None)

    _fill_hotel_names(by_tenant)
    return list(by_tenant.values())


def active_role_code(ctx: TenantContext, memberships: list[dict] | None = None) -> str:
    """Role code for the active context. SuperAdmin wins unless impersonating."""
    if ctx.is_superadmin and not ctx.impersonating:
        return "SUPER_ADMIN"
    tid = ctx.tenant_id
    for item in memberships or []:
        if item.get("tenant_id") == tid and item.get("role") and item["role"] != "SUPER_ADMIN":
            return item["role"]
    for name in ctx.roles:
        code = role_code_from_name(name)
        if code and code != "SUPER_ADMIN":
            return code
    if ctx.roles:
        return role_code_from_name(ctx.roles[0])
    return ""


def bind_request_tenant(
    conn: DbConn,
    ctx: TenantContext,
    header_value: str | None,
    *,
    property_header: str | None = None,
) -> TenantContext:
    """Apply ``X-Property-ID`` (or legacy ``X-Tenant-Id``) for the active branch.

    ``X-Property-ID: ALL`` is allowed for group owners/admins and scopes reads
    across every property in ``group_id`` (``isolation_id`` becomes None;
    callers must filter by group / assigned_properties).

    SuperAdmin and impersonation sessions ignore the header. A hotel user
    with no header keeps the tenant stored on their account. A header for a
    hotel they are not a member of is rejected.
    """
    from property_scope import (
        PROPERTY_ALL as _ALL,
        group_id_for_property,
        is_group_owner,
        list_properties_in_group,
        resolve_active_property_header,
    )
    import db as dbmod

    if ctx.is_superadmin or ctx.impersonating:
        return ctx

    requested = resolve_active_property_header(
        property_header=property_header,
        tenant_header=header_value,
    )
    memberships = load_memberships(conn, ctx.user_id)
    allowed = {item["tenant_id"] for item in memberships}
    if ctx.tenant_id:
        allowed.add(ctx.tenant_id)
    assigned = tuple(sorted(allowed))

    # Resolve group_id if missing (legacy users before backfill).
    group_id = ctx.group_id
    if not group_id and ctx.tenant_id:
        try:
            erp = dbmod.get_skill_db("erpclaw")
            if erp:
                try:
                    group_id = group_id_for_property(erp, ctx.tenant_id)
                finally:
                    erp.close()
        except Exception:
            group_id = ctx.group_id

    if not requested or requested == (ctx.tenant_id or ""):
        return TenantContext(
            user_id=ctx.user_id,
            email=ctx.email,
            tenant_id=ctx.tenant_id,
            is_superadmin=False,
            roles=ctx.roles,
            impersonating=False,
            group_id=group_id,
            assigned_properties=assigned,
            active_property_id=ctx.tenant_id,
            scope_all_properties=False,
        )

    if requested.upper() == _ALL:
        if not is_group_owner(ctx.roles):
            raise TenantIsolationError(
                "X-Property-ID=ALL requires Hotel Owner, Hotel Admin, General Manager, or Hotel Accountant",
                status_code=403,
            )
        if not group_id:
            raise TenantIsolationError(
                "No group_id on this account; cannot aggregate properties",
                status_code=400,
            )
        # Expand assigned list to every property in the group for owners.
        try:
            erp = dbmod.get_skill_db("erpclaw")
            if erp:
                try:
                    props = list_properties_in_group(erp, group_id)
                    group_props = tuple(sorted(str(p["property_id"]) for p in props))
                    if group_props:
                        assigned = group_props
                finally:
                    erp.close()
        except Exception:
            pass
        return TenantContext(
            user_id=ctx.user_id,
            email=ctx.email,
            tenant_id=ctx.tenant_id,
            is_superadmin=False,
            roles=ctx.roles,
            impersonating=False,
            group_id=group_id,
            assigned_properties=assigned,
            active_property_id=_ALL,
            scope_all_properties=True,
        )

    if requested not in allowed:
        # Owners may activate any property in their group even if membership
        # row is missing (new branch just created under the group).
        if group_id and is_group_owner(ctx.roles):
            try:
                erp = dbmod.get_skill_db("erpclaw")
                if erp:
                    try:
                        gid = group_id_for_property(erp, requested)
                        if gid == group_id:
                            allowed.add(requested)
                            assigned = tuple(sorted(allowed))
                    finally:
                        erp.close()
            except Exception:
                pass
        if requested not in allowed:
            raise TenantIsolationError(
                "property_id is not assigned to this user",
                status_code=403,
            )

    match = next((item for item in memberships if item["tenant_id"] == requested), None)
    roles = ctx.roles
    if match and match.get("role_name"):
        roles = (match["role_name"],)
    elif match and match.get("role"):
        roles = (match["role"],)
    return TenantContext(
        user_id=ctx.user_id,
        email=ctx.email,
        tenant_id=requested,
        is_superadmin=False,
        roles=roles,
        impersonating=False,
        group_id=group_id,
        assigned_properties=assigned,
        active_property_id=requested,
        scope_all_properties=False,
    )


def public_user(
    conn: DbConn,
    ctx: TenantContext,
    *,
    full_name: str | None,
    username: str | None = None,
    impersonated_hotel: str | None = None,
) -> dict:
    """Login /me payload: role, active tenant, and the hotels they can switch to."""
    from rbac import named_permissions_for_user

    if ctx.impersonating and ctx.tenant_id:
        memberships = [{
            "tenant_id": ctx.tenant_id,
            "hotel_name": impersonated_hotel or "Hotel",
            "role": "HOTEL_OWNER",
            "role_name": "Hotel Owner",
        }]
    elif ctx.is_superadmin:
        memberships = []
    else:
        memberships = load_memberships(conn, ctx.user_id)

    role = active_role_code(ctx, memberships)
    # Enrich memberships as properties for the new hierarchy contract.
    properties = [
        {
            "property_id": m.get("tenant_id"),
            "tenant_id": m.get("tenant_id"),  # legacy alias
            "hotel_name": m.get("hotel_name") or "",
            "role": m.get("role") or "",
            "role_name": m.get("role_name") or "",
        }
        for m in memberships
    ]
    user = {
        "user_id": ctx.user_id,
        "email": ctx.email,
        "full_name": full_name,
        "tenant_id": ctx.tenant_id,
        "property_id": None if ctx.scope_all_properties else (ctx.active_property_id or ctx.tenant_id),
        "group_id": ctx.group_id,
        "assigned_properties": list(ctx.assigned_properties or [m["tenant_id"] for m in memberships if m.get("tenant_id")]),
        "active_property_id": ctx.active_property_id or ctx.tenant_id,
        "scope_all_properties": bool(ctx.scope_all_properties),
        "role": role,
        "is_superadmin": ctx.is_superadmin and not ctx.impersonating,
        "is_impersonating": ctx.impersonating,
        "impersonated_hotel": impersonated_hotel,
        "impersonation_reason": ctx.impersonation_reason if ctx.impersonating else None,
        "roles": (
            [{"role_name": "Hotel Owner"}]
            if ctx.impersonating
            else [{"role_name": name} for name in (ctx.roles or ())]
        ),
        "permissions": named_permissions_for_user(
            conn,
            ctx.user_id,
            tenant_id=ctx.tenant_id if not ctx.is_superadmin else None,
            is_superadmin=ctx.is_superadmin and not ctx.impersonating,
            impersonating=ctx.impersonating,
        ),
        "tenants": memberships,  # legacy
        "properties": properties,
    }
    if username is not None:
        user["username"] = username
    # Partner / reseller console binding (email or user_id link)
    try:
        from partner_console import partner_for_user

        partner = partner_for_user(conn, ctx.user_id, ctx.email)
        if partner:
            user["partner_id"] = partner["id"]
            user["partner_name"] = partner["name"]
            if "partner.console.view" not in (user.get("permissions") or []):
                user["permissions"] = list(user.get("permissions") or []) + [
                    "partner.console.view"
                ]
    except Exception:
        pass
    return user
