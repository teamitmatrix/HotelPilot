"""Webclaw database schema — creates all webclaw-owned tables.

Tables:
  webclaw_user          — Web UI user accounts (separate from skill-level users)
  webclaw_session       — JWT refresh token sessions
  webclaw_config        — Key-value config (JWT secret, etc.)
  webclaw_role          — RBAC roles
  webclaw_user_role     — User ↔ role assignments
  webclaw_role_permission — Role ↔ skill/action permissions
  chat_session          — AI chat sessions
  chat_message          — AI chat messages
  audit_log             — POST action audit trail
  adaptive_profile      — Business profile (dental, hospital, etc.)
  skill_activation      — Per-profile skill enable/disable log
  usage_counter         — Entity counts for expansion triggers
  expansion_prompt      — Pending/resolved module suggestions
  vocabulary_map        — Profile-specific term mappings

Idempotent: uses CREATE TABLE IF NOT EXISTS throughout.
"""
from __future__ import annotations

from db_access import DbConn

SCHEMA_DDL = """
-- ── Auth ────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS webclaw_user (
    id              TEXT PRIMARY KEY,
    username        TEXT NOT NULL UNIQUE,
    email           TEXT UNIQUE,
    full_name       TEXT,
    password_hash   TEXT,
    status          TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled','locked')),
    failed_login_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until    TEXT,
    last_login      TEXT,
    tenant_id       TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS webclaw_session (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL REFERENCES webclaw_user(id) ON DELETE CASCADE,
    refresh_token_hash  TEXT NOT NULL UNIQUE,
    expires_at          TEXT NOT NULL,
    created_at          TEXT DEFAULT (datetime('now')),
    last_active_at      TEXT DEFAULT (datetime('now')),
    ip_address          TEXT,
    user_agent          TEXT
);

CREATE INDEX IF NOT EXISTS idx_webclaw_session_user ON webclaw_session(user_id);
CREATE INDEX IF NOT EXISTS idx_webclaw_session_expires ON webclaw_session(expires_at);

CREATE TABLE IF NOT EXISTS webclaw_config (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT DEFAULT (datetime('now'))
);

-- ── RBAC ────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS webclaw_role (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    code            TEXT UNIQUE,
    description     TEXT,
    is_system       INTEGER NOT NULL DEFAULT 0 CHECK(is_system IN (0,1)),
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS webclaw_user_role (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES webclaw_user(id) ON DELETE CASCADE,
    role_id         TEXT NOT NULL REFERENCES webclaw_role(id) ON DELETE CASCADE,
    tenant_id       TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, role_id)
);

CREATE INDEX IF NOT EXISTS idx_webclaw_user_role_user ON webclaw_user_role(user_id);
CREATE INDEX IF NOT EXISTS idx_webclaw_user_role_role ON webclaw_user_role(role_id);

CREATE TABLE IF NOT EXISTS webclaw_user_tenant (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    tenant_id       TEXT NOT NULL,
    role_code       TEXT,
    role_name       TEXT,
    hotel_name      TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_webclaw_user_tenant_user ON webclaw_user_tenant(user_id);

CREATE TABLE IF NOT EXISTS webclaw_role_permission (
    id              TEXT PRIMARY KEY,
    role_id         TEXT NOT NULL REFERENCES webclaw_role(id) ON DELETE CASCADE,
    skill           TEXT NOT NULL,
    action_pattern  TEXT NOT NULL,
    allowed         INTEGER NOT NULL DEFAULT 1 CHECK(allowed IN (0,1)),
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(role_id, skill, action_pattern)
);

CREATE INDEX IF NOT EXISTS idx_webclaw_role_perm_role ON webclaw_role_permission(role_id);
CREATE INDEX IF NOT EXISTS idx_webclaw_role_perm_skill ON webclaw_role_permission(skill);

-- ── Chat ────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS chat_session (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES webclaw_user(id) ON DELETE CASCADE,
    title       TEXT,
    context     TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_chat_session_user ON chat_session(user_id);

CREATE TABLE IF NOT EXISTS chat_message (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES chat_session(id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
    content     TEXT NOT NULL,
    context     TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_chat_message_session ON chat_message(session_id);

-- ── Audit ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS audit_log (
    id              TEXT PRIMARY KEY,
    timestamp       TEXT DEFAULT (datetime('now')),
    user_id         TEXT,
    skill           TEXT NOT NULL,
    action          TEXT NOT NULL,
    entity_type     TEXT,
    entity_id       TEXT,
    description     TEXT,
    request_id      TEXT,
    created_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_log_user ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_skill ON audit_log(skill);

-- ── Adaptive ERP ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS adaptive_profile (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES webclaw_user(id) ON DELETE CASCADE,
    profile_key     TEXT NOT NULL,
    display_name    TEXT NOT NULL,
    active_skills   TEXT NOT NULL DEFAULT '[]',
    vocabulary_overrides TEXT DEFAULT '{}',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_adaptive_profile_user ON adaptive_profile(user_id);

CREATE TABLE IF NOT EXISTS skill_activation (
    id              TEXT PRIMARY KEY,
    profile_id      TEXT NOT NULL REFERENCES adaptive_profile(id) ON DELETE CASCADE,
    skill_name      TEXT NOT NULL,
    activated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    activated_by    TEXT NOT NULL CHECK(activated_by IN ('onboarding','expansion','manual')),
    UNIQUE(profile_id, skill_name)
);

CREATE INDEX IF NOT EXISTS idx_skill_activation_profile ON skill_activation(profile_id);

CREATE TABLE IF NOT EXISTS usage_counter (
    id              TEXT PRIMARY KEY,
    profile_id      TEXT NOT NULL REFERENCES adaptive_profile(id) ON DELETE CASCADE,
    entity_type     TEXT NOT NULL,
    skill_name      TEXT NOT NULL,
    count           INTEGER NOT NULL DEFAULT 0,
    last_updated    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(profile_id, entity_type, skill_name)
);

CREATE INDEX IF NOT EXISTS idx_usage_counter_profile ON usage_counter(profile_id);

CREATE TABLE IF NOT EXISTS expansion_prompt (
    id              TEXT PRIMARY KEY,
    profile_id      TEXT NOT NULL REFERENCES adaptive_profile(id) ON DELETE CASCADE,
    trigger_rule    TEXT NOT NULL,
    suggested_skill TEXT NOT NULL,
    message         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','dismissed')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_expansion_prompt_profile ON expansion_prompt(profile_id);
CREATE INDEX IF NOT EXISTS idx_expansion_prompt_status ON expansion_prompt(status);

CREATE TABLE IF NOT EXISTS vocabulary_map (
    profile_key     TEXT NOT NULL,
    standard_term   TEXT NOT NULL,
    adapted_term    TEXT NOT NULL,
    PRIMARY KEY (profile_key, standard_term)
);

-- ── Platform subscription plans (SuperAdmin) ────────────────────────────────

CREATE TABLE IF NOT EXISTS webclaw_subscription_plan (
    id              TEXT PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    billing_cycle   TEXT NOT NULL DEFAULT 'monthly'
                    CHECK(billing_cycle IN ('monthly','annual')),
    monthly_price   TEXT NOT NULL DEFAULT '0',
    annual_price    TEXT NOT NULL DEFAULT '0',
    max_rooms       INTEGER NOT NULL DEFAULT 20,
    features        TEXT NOT NULL DEFAULT '{}',
    is_active       INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
    sort_order      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);
"""


def _migrate_webclaw_user(conn: DbConn) -> None:
    """Add lockout + tenant columns to webclaw_user if missing (migration)."""
    from db_access import table_columns, DbConn

    cols = table_columns(conn, "webclaw_user")
    if "failed_login_attempts" not in cols:
        try:
            conn.execute("ALTER TABLE webclaw_user ADD COLUMN failed_login_attempts INTEGER NOT NULL DEFAULT 0")
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
    if "locked_until" not in cols:
        try:
            conn.execute("ALTER TABLE webclaw_user ADD COLUMN locked_until TEXT")
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
    if "tenant_id" not in cols:
        try:
            conn.execute("ALTER TABLE webclaw_user ADD COLUMN tenant_id TEXT")
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_webclaw_user_tenant ON webclaw_user(tenant_id)")
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


def _migrate_audit_log(conn: DbConn) -> None:
    """Add request_id and created_at columns to audit_log if missing (migration)."""
    from db_access import table_columns

    cols = table_columns(conn, "audit_log")
    if "request_id" not in cols:
        try:
            conn.execute("ALTER TABLE audit_log ADD COLUMN request_id TEXT")
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
    if "created_at" not in cols:
        try:
            conn.execute("ALTER TABLE audit_log ADD COLUMN created_at TEXT")
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass


def init_tables(conn: DbConn) -> None:
    """Create all webclaw tables if they don't exist. Idempotent."""
    from db_access import run_ddl

    run_ddl(conn, SCHEMA_DDL)
    _migrate_webclaw_user(conn)
    _migrate_audit_log(conn)
    from rbac import migrate_rbac_schema, seed_rbac
    migrate_rbac_schema(conn)
    seed_rbac(conn)
    try:
        from admin_platform import ensure_subscription_plans
        ensure_subscription_plans(conn)
    except Exception:
        pass
