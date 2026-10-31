"""Self-service hotel onboarding — creates a tenant, ERP company, and owner user."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from auth.jwt_utils import (
    create_refresh_token,
    get_signing_secret,
    issue_access_token,
    REFRESH_TOKEN_EXPIRY,
)
from auth.passwords import hash_password, validate_password_strength
import db
from skills.executor import execute_skill
from rbac import HOTEL_OWNER, ROLE_PERMISSIONS

HOTEL_OWNER_ROLE = "Hotel Owner"
ONBOARD_TIMEOUT = 180
PLAN_TO_TIER = {"starter": "starter", "business": "professional", "enterprise": "enterprise"}
TIER_TO_PLAN = {
    "starter": "starter",
    "professional": "business",
    "business": "business",
    "enterprise": "enterprise",
}


def _seed_default_tax_template(tenant_id: str) -> None:
    """Create a default occupancy tax template on the ERP tax tables (platform)."""
    skill_db = db.get_skill_db("erpclaw")
    if not skill_db:
        return
    try:
        acct = skill_db.execute(
            "SELECT id FROM account WHERE company_id = ? AND account_number = ? AND is_group = 0 LIMIT 1",
            (tenant_id, "2121"),
        ).fetchone()
        if not acct:
            return
        existing = skill_db.execute(
            "SELECT id FROM tax_template WHERE company_id = ? AND name = ?",
            (tenant_id, "Default Occupancy Tax"),
        ).fetchone()
        if existing:
            return
        template_id = str(uuid.uuid4())
        skill_db.execute(
            "INSERT INTO tax_template (id, name, tax_type, is_default, company_id) VALUES (?, ?, 'sales', 1, ?)",
            (template_id, "Default Occupancy Tax", tenant_id),
        )
        skill_db.execute(
            """INSERT INTO tax_template_line
               (id, tax_template_id, tax_account_id, rate, charge_type, row_order, add_deduct)
               VALUES (?, ?, ?, '10', 'on_net_total', 0, 'add')""",
            (str(uuid.uuid4()), template_id, acct[0] if not hasattr(acct, "keys") else acct["id"]),
        )
        skill_db.commit()
    except Exception:
        pass
    finally:
        skill_db.close()


def _validate_timezone(tz: str) -> str | None:
    try:
        ZoneInfo(tz)
        return None
    except ZoneInfoNotFoundError:
        return f"Unknown timezone: {tz}"


def _role_id(conn, name: str) -> str | None:
    row = conn.execute("SELECT id FROM webclaw_role WHERE name = ?", (name,)).fetchone()
    if not row:
        return None
    return row[0] if isinstance(row, tuple) else row["id"]


def _ensure_hotel_owner_role(conn) -> str | None:
    """Seed RBAC if needed and return the Hotel Owner role id."""
    try:
        from rbac import seed_rbac
        seed_rbac(conn)
    except Exception:
        pass
    role_id = _role_id(conn, HOTEL_OWNER_ROLE)
    if role_id:
        return role_id
    role_id = str(uuid.uuid4())
    from db_access import table_columns

    cols = table_columns(conn, "webclaw_role")
    if "code" in cols:
        conn.execute(
            """INSERT INTO webclaw_role (id, name, code, description, is_system)
               VALUES (?, ?, 'HOTEL_OWNER', 'Hotel administrator', 1)""",
            (role_id, HOTEL_OWNER_ROLE),
        )
    else:
        conn.execute(
            "INSERT INTO webclaw_role (id, name, description, is_system) VALUES (?, ?, ?, 1)",
            (role_id, HOTEL_OWNER_ROLE, "Hotel administrator"),
        )
    return role_id


async def register_hotel(
    *,
    hotel_name: str,
    admin_email: str,
    password: str,
    currency: str,
    country: str,
    timezone_name: str,
    subscription_tier: str,
    ip: str,
    user_agent: str,
    onboarding_source: str = "self_serve",
) -> dict:
    """Provision a new hotel tenant and return tokens + tenant metadata.

    Steps:
      1. Validate password + timezone; reject duplicate email.
      2. Allocate a tenant_id UUID and run ``setup-company`` for that tenant.
      3. ``seed-defaults`` + ``setup-chart-of-accounts`` scoped to that UUID.
      4. Hospitality schema + default room types / tax templates.
      5. WebClaw Hotel Owner account with full hotel-level permissions.
    """
    pw_err = validate_password_strength(password)
    if pw_err:
        return {"status": "error", "message": pw_err, "http_status": 400}

    tz_err = _validate_timezone(timezone_name)
    if tz_err:
        return {"status": "error", "message": tz_err, "http_status": 400}

    stored_tier = PLAN_TO_TIER.get(subscription_tier, subscription_tier)
    if stored_tier not in ("starter", "professional", "enterprise"):
        stored_tier = "starter"
    plan_code = TIER_TO_PLAN.get(stored_tier, "starter")

    conn = db.get_connection()
    existing = conn.execute(
        "SELECT id FROM webclaw_user WHERE email = ?", (admin_email,)
    ).fetchone()
    if existing:
        return {"status": "error", "message": "An account with this email already exists", "http_status": 409}

    tenant_id = str(uuid.uuid4())

    # Fresh ERP files have no tables yet — migrate alone cannot create `company`.
    init = await execute_skill("erpclaw", "initialize-database", {}, timeout=ONBOARD_TIMEOUT)
    if init.get("status") != "ok":
        return {
            "status": "error",
            "message": init.get("message") or init.get("error") or "Could not initialize ERP database",
            "detail": init,
            "http_status": 500,
        }

    # migrate is gated as DANGEROUS_ACTIONS; platform onboarding is the
    # confirmed operator. Soft-fail: initialize-database already provisions the
    # current schema, and some early migrations are still SQLite-oriented.
    migrate = await execute_skill(
        "erpclaw",
        "migrate",
        {"user-confirmed": True},
        timeout=60,
    )
    migrate_failed = []
    if migrate.get("status") != "ok":
        migrate_failed.append({
            "step": "migrate",
            "result": migrate,
        })

    setup = await execute_skill(
        "erpclaw",
        "setup-company",
        {
            "name": hotel_name,
            "currency": currency,
            "country": country,
            "timezone": timezone_name,
            "admin-email": admin_email,
            "subscription-tier": stored_tier,
            "company-id": tenant_id,
        },
        timeout=ONBOARD_TIMEOUT,
    )
    if setup.get("status") != "ok":
        return {
            "status": "error",
            "message": setup.get("message") or setup.get("error") or "Company setup failed",
            "detail": setup,
            "http_status": 400,
        }

    tenant_id = setup.get("company_id") or tenant_id
    if not tenant_id:
        return {"status": "error", "message": "setup-company did not return a company_id", "http_status": 500}

    seeded = []
    failed = list(migrate_failed)

    defaults = await execute_skill(
        "erpclaw",
        "seed-defaults",
        {"company-id": tenant_id},
        timeout=ONBOARD_TIMEOUT,
        tenant_id=tenant_id,
    )
    (seeded if defaults.get("status") == "ok" else failed).append(
        {"step": "seed-defaults", "result": defaults}
    )

    coa = await execute_skill(
        "erpclaw",
        "setup-chart-of-accounts",
        {"company-id": tenant_id, "template": "us_gaap"},
        timeout=ONBOARD_TIMEOUT,
        tenant_id=tenant_id,
    )
    (seeded if coa.get("status") == "ok" else failed).append(
        {"step": "setup-chart-of-accounts", "result": coa}
    )

    hosp_init = await execute_skill(
        "hospitalityclaw",
        "hospitality-initialize-schema",
        {"tenant-id": tenant_id},
        timeout=ONBOARD_TIMEOUT,
        tenant_id=tenant_id,
    )
    (seeded if hosp_init.get("status") == "ok" else failed).append(
        {"step": "hospitality-initialize-schema", "result": hosp_init}
    )

    hosp_seed = await execute_skill(
        "hospitalityclaw",
        "hospitality-seed-defaults",
        {
            "tenant-id": tenant_id,
            "hotel-name": hotel_name,
            "currency": currency,
            "country": country,
            "timezone": timezone_name,
        },
        timeout=ONBOARD_TIMEOUT,
        tenant_id=tenant_id,
    )
    (seeded if hosp_seed.get("status") == "ok" else failed).append(
        {"step": "hospitality-seed-defaults", "result": hosp_seed}
    )

    _seed_default_tax_template(tenant_id)

    # Upsert pms_tenant + create parent group (property_id == tenant_id / company.id).
    from property_scope import (
        attach_property_to_group,
        create_group,
        ensure_group_schema_erp,
        ensure_group_schema_webclaw,
        group_id_for_property,
    )

    group_id: str | None = None
    skill_db = db.get_skill_db("erpclaw")
    if skill_db:
        try:
            ensure_group_schema_erp(skill_db)
            skill_db.execute(
                """INSERT INTO pms_tenant
                   (id, company_id, hotel_name, admin_email, timezone, country,
                    currency, subscription_tier, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')
                   ON CONFLICT(id) DO UPDATE SET
                     hotel_name=excluded.hotel_name,
                     admin_email=excluded.admin_email,
                     timezone=excluded.timezone,
                     country=excluded.country,
                     currency=excluded.currency,
                     subscription_tier=excluded.subscription_tier,
                     updated_at=CURRENT_TIMESTAMP""",
                (
                    tenant_id, tenant_id, hotel_name, admin_email,
                    timezone_name, country, currency, stored_tier,
                ),
            )
            skill_db.commit()
            group_id = group_id_for_property(skill_db, tenant_id)
            if not group_id:
                group_id = create_group(
                    skill_db,
                    name=f"{hotel_name} Group",
                    admin_email=admin_email,
                )
            attach_property_to_group(skill_db, tenant_id, group_id)
        except Exception:
            pass
        finally:
            skill_db.close()

    ensure_group_schema_webclaw(conn)
    user_id = str(uuid.uuid4())
    username = admin_email.split("@")[0]
    from db_access import table_columns

    user_cols = table_columns(conn, "webclaw_user")
    try:
        if "group_id" in user_cols:
            conn.execute(
                """INSERT INTO webclaw_user
                   (id, username, email, full_name, password_hash, status, tenant_id, group_id)
                   VALUES (?, ?, ?, ?, ?, 'active', ?, ?)""",
                (
                    user_id, username, admin_email, hotel_name,
                    hash_password(password), tenant_id, group_id,
                ),
            )
        else:
            conn.execute(
                """INSERT INTO webclaw_user
                   (id, username, email, full_name, password_hash, status, tenant_id)
                   VALUES (?, ?, ?, ?, ?, 'active', ?)""",
                (user_id, username, admin_email, hotel_name, hash_password(password), tenant_id),
            )
    except Exception:
        # Username collision — suffix a short uuid.
        username = f"{username}-{user_id[:8]}"
        if "group_id" in user_cols:
            conn.execute(
                """INSERT INTO webclaw_user
                   (id, username, email, full_name, password_hash, status, tenant_id, group_id)
                   VALUES (?, ?, ?, ?, ?, 'active', ?, ?)""",
                (
                    user_id, username, admin_email, hotel_name,
                    hash_password(password), tenant_id, group_id,
                ),
            )
        else:
            conn.execute(
                """INSERT INTO webclaw_user
                   (id, username, email, full_name, password_hash, status, tenant_id)
                   VALUES (?, ?, ?, ?, ?, 'active', ?)""",
                (user_id, username, admin_email, hotel_name, hash_password(password), tenant_id),
            )

    role_id = _ensure_hotel_owner_role(conn)
    if role_id:
        cols = table_columns(conn, "webclaw_user_role")
        if "tenant_id" in cols:
            conn.execute(
                "INSERT INTO webclaw_user_role (id, user_id, role_id, tenant_id) VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), user_id, role_id, tenant_id),
            )
        else:
            conn.execute(
                "INSERT INTO webclaw_user_role (id, user_id, role_id) VALUES (?, ?, ?)",
                (str(uuid.uuid4()), user_id, role_id),
            )

    from tenant import upsert_membership
    upsert_membership(
        conn,
        user_id,
        tenant_id,
        role_code="HOTEL_OWNER",
        role_name=HOTEL_OWNER_ROLE,
        hotel_name=hotel_name,
    )

    access_token = issue_access_token(conn, user_id, admin_email)
    secret = get_signing_secret(conn)
    raw_refresh, refresh_hash = create_refresh_token(user_id, secret)

    conn.execute(
        """INSERT INTO webclaw_session
           (id, user_id, refresh_token_hash, expires_at, ip_address, user_agent)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()),
            user_id,
            refresh_hash,
            (datetime.now(timezone.utc) + REFRESH_TOKEN_EXPIRY).isoformat(),
            ip,
            user_agent,
        ),
    )
    conn.commit()

    # Start onboarding pipeline (checklist + go-live gates)
    try:
        from admin_onboarding import start_onboarding

        start_onboarding(
            conn,
            tenant_id=tenant_id,
            hotel_name=hotel_name,
            admin_email=admin_email,
            source=onboarding_source if onboarding_source in ("admin", "self_serve") else "self_serve",
            owner_already_logged_in=(onboarding_source == "self_serve"),
        )
    except Exception:
        pass

    return {
        "status": "ok",
        "access_token": access_token,
        "refresh_token": raw_refresh,
        "user": {
            "user_id": user_id,
            "email": admin_email,
            "full_name": hotel_name,
            "tenant_id": tenant_id,
            "property_id": tenant_id,
            "group_id": group_id,
            "assigned_properties": [tenant_id],
            "active_property_id": tenant_id,
            "role": "HOTEL_OWNER",
            "is_superadmin": False,
            "roles": [{"role_name": HOTEL_OWNER_ROLE}],
            "permissions": list(ROLE_PERMISSIONS[HOTEL_OWNER]),
            "tenants": [{
                "tenant_id": tenant_id,
                "property_id": tenant_id,
                "hotel_name": hotel_name,
                "role": "HOTEL_OWNER",
                "role_name": HOTEL_OWNER_ROLE,
            }],
        },
        "tenant": {
            "tenant_id": tenant_id,
            "property_id": tenant_id,
            "hotel_name": hotel_name,
            "group_id": group_id,
            "currency": currency,
            "country": country,
            "timezone": timezone_name,
            "subscription_tier": stored_tier,
            "plan_code": plan_code,
        },
        "setup": setup,
        "seeded_steps": [s["step"] for s in seeded],
        "failed_steps": [{"step": f["step"], "message": f["result"].get("message")} for f in failed],
        "http_status": 200,
        "message": "Hotel registered. You are now logged in.",
    }
