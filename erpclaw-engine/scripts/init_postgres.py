#!/usr/bin/env python3
"""Create the PMS schema on PostgreSQL.

Applies the ERPClaw, HospitalityClaw, and WebClaw DDL with PostgreSQL
types (SMALLINT flags, DATE, TIMESTAMP WITH TIME ZONE, JSONB, BIGSERIAL
where a SQLite AUTOINCREMENT key exists). UUID primary keys stay TEXT.
Also creates WebClaw RBAC, ops, and billing tables that SQLite creates
lazily via ensure_* helpers. Foreign keys, primary keys, and indexes are
created with the tables.

Credentials come from DB_HOST, DB_PORT, DB_NAME, DB_USER, and
DB_PASSWORD. Host defaults to localhost and port to 5432. This command
sets DB_TYPE=postgres for its own process.

From the pms/ workspace root:

    python erpclaw-engine/scripts/init_postgres.py
    python erpclaw-engine/scripts/init_postgres.py --print-sql
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "erpclaw-engine" / "scripts" / "erpclaw-setup" / "lib"
SETUP = ROOT / "erpclaw-engine" / "scripts" / "erpclaw-setup"


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = os.path.expanduser(value)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _webclaw_extra_modules():
    """RBAC / ops / billing modules (lazy on SQLite, explicit on Postgres)."""
    api = ROOT / "webclaw" / "api"
    if str(api) not in sys.path:
        sys.path.insert(0, str(api))
    rbac = _load_module("webclaw_rbac", api / "rbac.py")
    admin_ops = _load_module("webclaw_admin_ops", api / "admin_ops.py")
    admin_platform = _load_module("webclaw_admin_platform", api / "admin_platform.py")
    stripe_billing = _load_module("webclaw_stripe_billing", api / "stripe_billing.py")
    return rbac, admin_ops, admin_platform, stripe_billing


def _adapted_sections():
    from erpclaw_lib.pg_ddl import adapt_ddl_for_postgres
    import init_schema

    hospitality = _load_module(
        "hospitality_init_db",
        ROOT / "hospitalityclaw" / "init_db.py",
    )
    webclaw = _load_module(
        "webclaw_init_db",
        ROOT / "webclaw" / "api" / "init_webclaw_db.py",
    )
    rbac, admin_ops, admin_platform, stripe_billing = _webclaw_extra_modules()
    sections = []
    for skill_name, ddl in init_schema.ALL_DDL_BLOCKS:
        sections.append((f"erpclaw:{skill_name}", adapt_ddl_for_postgres(ddl)))
    sections.append(("hospitalityclaw", adapt_ddl_for_postgres(hospitality.HOSPITALITY_DDL)))
    sections.append(("hospitalityclaw:night_audit", adapt_ddl_for_postgres(hospitality.NIGHT_AUDIT_DDL)))
    sections.append(("webclaw", adapt_ddl_for_postgres(webclaw.SCHEMA_DDL)))
    sections.append(("webclaw:rbac", adapt_ddl_for_postgres(rbac.RBAC_DDL)))
    sections.append(("webclaw:ops", adapt_ddl_for_postgres(admin_ops.OPS_DDL)))
    sections.append(("webclaw:billing", adapt_ddl_for_postgres(admin_platform.BILLING_DDL)))
    sections.append(("webclaw:stripe", adapt_ddl_for_postgres(stripe_billing.STRIPE_DDL)))
    return sections


def _print_sql() -> None:
    for name, ddl in _adapted_sections():
        print(f"-- ===== {name} =====")
        print(ddl)
        if not ddl.endswith("\n"):
            print()


def _execute() -> None:
    os.environ["DB_TYPE"] = "postgres"
    import init_schema
    from erpclaw_lib.db import get_connection
    from erpclaw_lib.pg_ddl import adapt_ddl_for_postgres

    host = (os.environ.get("DB_HOST") or "localhost").strip() or "localhost"
    port = (os.environ.get("DB_PORT") or "5432").strip() or "5432"
    name = (os.environ.get("DB_NAME") or "pms").strip() or "pms"
    user = (os.environ.get("DB_USER") or "postgres").strip() or "postgres"
    target = os.environ.get("ERPCLAW_DB_URL") or f"postgresql://{user}@{host}:{port}/{name}"
    print(f"Creating schema on {init_schema.redact_db_url(target)}", file=sys.stderr)
    init_schema.init_db()

    hospitality = _load_module(
        "hospitality_init_db",
        ROOT / "hospitalityclaw" / "init_db.py",
    )
    webclaw = _load_module(
        "webclaw_init_db",
        ROOT / "webclaw" / "api" / "init_webclaw_db.py",
    )
    rbac, admin_ops, admin_platform, stripe_billing = _webclaw_extra_modules()
    conn = get_connection()
    try:
        conn.execute(adapt_ddl_for_postgres(hospitality.HOSPITALITY_DDL))
        conn.execute(adapt_ddl_for_postgres(hospitality.NIGHT_AUDIT_DDL))
        # Lazy ERP extras (property groups, channel manager, revenue yield).
        api = ROOT / "webclaw" / "api"
        if str(api) not in sys.path:
            sys.path.insert(0, str(api))
        property_scope = _load_module("webclaw_property_scope", api / "property_scope.py")
        channel_manager = _load_module(
            "webclaw_channel_manager", api / "services" / "channel_manager.py"
        )
        revenue = _load_module("webclaw_revenue", api / "services" / "revenue.py")
        # pms_group DDL is embedded in ensure_group_schema_erp — extract CREATE block.
        pms_group_ddl = """
CREATE TABLE IF NOT EXISTS pms_group (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    admin_email     TEXT,
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK(status IN ('active','paused','cancelled')),
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pms_group_status ON pms_group(status);
"""
        conn.execute(adapt_ddl_for_postgres(pms_group_ddl))
        conn.execute(adapt_ddl_for_postgres(channel_manager.CHANNEL_DDL))
        conn.execute(adapt_ddl_for_postgres(revenue.REVENUE_DDL))
        for table in ("company", "pms_tenant"):
            exists = conn.execute(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = ?
                  AND column_name = 'group_id'
                """,
                (table,),
            ).fetchone()
            table_exists = conn.execute(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = ?
                """,
                (table,),
            ).fetchone()
            if table_exists and not exists:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN group_id TEXT")
                conn.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{table}_group ON {table}(group_id)"
                )
        conn.commit()
        print("ERP extras: pms_group + channel + revenue", file=sys.stderr)
        # silence unused-import style linters for property_scope load side effects
        _ = property_scope
        conn.execute("CREATE SCHEMA IF NOT EXISTS webclaw")
        conn.execute("SET search_path TO webclaw")
        conn.execute(adapt_ddl_for_postgres(webclaw.SCHEMA_DDL))
        conn.execute(adapt_ddl_for_postgres(rbac.RBAC_DDL))
        conn.execute(adapt_ddl_for_postgres(admin_ops.OPS_DDL))
        conn.execute(adapt_ddl_for_postgres(admin_platform.BILLING_DDL))
        conn.execute(adapt_ddl_for_postgres(stripe_billing.STRIPE_DDL))
        # Stripe Price IDs on plan catalog (SQLite adds these lazily).
        for col in (
            "stripe_product_id",
            "stripe_price_monthly_id",
            "stripe_price_annual_id",
            "max_staff",
        ):
            exists = conn.execute(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'webclaw'
                  AND table_name = 'webclaw_subscription_plan'
                  AND column_name = ?
                """,
                (col,),
            ).fetchone()
            if not exists:
                if col == "max_staff":
                    conn.execute(
                        "ALTER TABLE webclaw_subscription_plan "
                        "ADD COLUMN max_staff INTEGER NOT NULL DEFAULT 5"
                    )
                else:
                    conn.execute(
                        f"ALTER TABLE webclaw_subscription_plan ADD COLUMN {col} TEXT"
                    )
        user_group = conn.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'webclaw'
              AND table_name = 'webclaw_user'
              AND column_name = 'group_id'
            """
        ).fetchone()
        if not user_group:
            conn.execute("ALTER TABLE webclaw_user ADD COLUMN group_id TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_webclaw_user_group "
                "ON webclaw_user(group_id)"
            )
        conn.commit()
        print("WebClaw extras: rbac + ops + billing + stripe", file=sys.stderr)
        rows = conn.execute(
            """
            SELECT n.nspname AS schema_name,
                   c.relkind,
                   COUNT(*) AS n
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname IN ('public', 'webclaw')
              AND c.relkind IN ('r', 'i')
            GROUP BY 1, 2
            ORDER BY 1, 2
            """
        ).fetchall()
        constraints = conn.execute(
            """
            SELECT table_schema, constraint_type, COUNT(*)
            FROM information_schema.table_constraints
            WHERE table_schema IN ('public', 'webclaw')
              AND constraint_type IN ('PRIMARY KEY', 'FOREIGN KEY', 'UNIQUE')
            GROUP BY 1, 2
            ORDER BY 1, 2
            """
        ).fetchall()
    finally:
        conn.close()

    print("Objects:", file=sys.stderr)
    for schema_name, relkind, count in rows:
        kind = "tables" if relkind == "r" else "indexes"
        print(f"  {schema_name} {kind}: {count}", file=sys.stderr)
    print("Constraints:", file=sys.stderr)
    for schema_name, constraint_type, count in constraints:
        print(f"  {schema_name} {constraint_type}: {count}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the PMS schema on PostgreSQL")
    parser.add_argument(
        "--print-sql",
        action="store_true",
        help="Print the adapted DDL and do not connect",
    )
    args = parser.parse_args()
    if str(LIB) not in sys.path:
        sys.path.insert(0, str(LIB))
    if str(SETUP) not in sys.path:
        sys.path.insert(0, str(SETUP))
    _load_env_file(ROOT / "webclaw" / "api" / ".env")
    _load_env_file(ROOT / "erpclaw-engine" / ".env")
    if args.print_sql:
        _print_sql()
        return
    _execute()


if __name__ == "__main__":
    main()
