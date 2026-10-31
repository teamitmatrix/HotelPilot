"""Migration 037: PMS tenant registry + company_id (tenant) indexes.

Row-level multi-tenancy for the hotel PMS. The tenant UUID is company.id.
This migration:

  1. Creates ``pms_tenant`` (subscription, timezone, admin email) keyed by
     the same UUID as ``company.id``.
  2. Adds ``timezone`` on ``company`` when missing.
  3. Ensures ``company_id`` indexes exist on core operational tables so
     ``WHERE company_id = :tenant_id`` stays cheap as hotels accumulate.
  4. Creates ``v_gl_entry_tenant`` so GL rows (which inherit tenant via
     account.company_id) can be filtered without a join at every call site.

Does NOT add a duplicate ``tenant_id`` column to every ERP table — that
would be a second key that drifts from ``company_id``. Hospitality tables
use ``tenant_id`` and live in the hospitalityclaw module installer.

Idempotent. SQLite + PostgreSQL. No row rewrites.
"""
import argparse
import importlib.util
import os
import sys

MIGRATION_DATA_CLASS = "none"

if importlib.util.find_spec("erpclaw_lib") is None:  # pragma: no cover
    sys.path.insert(0, os.path.join(
        os.path.expanduser(os.environ.get("ERPCLAW_HOME", "~/.openclaw/erpclaw")), "lib"))

from erpclaw_lib import seam  # noqa: E402
from erpclaw_lib.db import get_connection  # noqa: E402
from erpclaw_lib.paths import db_default  # noqa: E402

DEFAULT_DB_PATH = db_default()

_CREATE_PMS_TENANT = """
CREATE TABLE IF NOT EXISTS pms_tenant (
    id                  TEXT PRIMARY KEY,
    company_id          TEXT NOT NULL UNIQUE REFERENCES company(id) ON DELETE RESTRICT,
    hotel_name          TEXT NOT NULL,
    admin_email         TEXT NOT NULL,
    timezone            TEXT NOT NULL DEFAULT 'UTC',
    country             TEXT NOT NULL DEFAULT 'United States',
    currency            TEXT NOT NULL DEFAULT 'USD',
    subscription_tier   TEXT NOT NULL DEFAULT 'starter'
                        CHECK(subscription_tier IN ('starter','professional','enterprise')),
    status              TEXT NOT NULL DEFAULT 'active'
                        CHECK(status IN ('active','suspended','cancelled')),
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
)
"""

_IDX_PMS_TENANT_STATUS = (
    "CREATE INDEX IF NOT EXISTS idx_pms_tenant_status ON pms_tenant(status)"
)
_IDX_PMS_TENANT_EMAIL = (
    "CREATE INDEX IF NOT EXISTS idx_pms_tenant_admin_email ON pms_tenant(admin_email)"
)

_ADD_COMPANY_TIMEZONE = "ALTER TABLE company ADD COLUMN timezone TEXT NOT NULL DEFAULT 'UTC'"

# Core ERP tables that are tenant-scoped via company_id. CREATE INDEX IF NOT
# EXISTS is a no-op when init_schema already created the same name.
_COMPANY_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_account_company ON account(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_cost_center_company ON cost_center(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_fiscal_year_company ON fiscal_year(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_journal_entry_company ON journal_entry(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_payment_entry_company ON payment_entry(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_tax_template_company ON tax_template(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_tax_rule_company ON tax_rule(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_customer_company ON customer(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_supplier_company ON supplier(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_sales_invoice_company ON sales_invoice(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_purchase_invoice_company ON purchase_invoice(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_warehouse_company ON warehouse(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_employee_company ON employee(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_naming_series_company ON naming_series(company_id)",
)

_CREATE_GL_TENANT_VIEW = """
CREATE VIEW IF NOT EXISTS v_gl_entry_tenant AS
SELECT g.*, a.company_id AS tenant_id
FROM gl_entry g
JOIN account a ON a.id = g.account_id
"""


def run_migration(db_path=None, report_only=False):
    path = db_path or os.environ.get("ERPCLAW_DB_PATH", DEFAULT_DB_PATH)
    conn = get_connection(path)
    created = []
    skipped = []
    try:
        if not seam.table_exists("company", path):
            print("  company table missing — initialize-database first. nothing to migrate.")
            return {"created": [], "skipped": ["no company table"]}

        if report_only:
            print("  report-only: would ensure pms_tenant, company.timezone, "
                  "company_id indexes, v_gl_entry_tenant")
            return {"report_only": True}

        conn.execute(_CREATE_PMS_TENANT)
        created.append("pms_tenant")
        conn.execute(_IDX_PMS_TENANT_STATUS)
        conn.execute(_IDX_PMS_TENANT_EMAIL)

        cols = set(seam.column_names("company", path) or [])
        if "timezone" not in cols:
            conn.execute(_ADD_COMPANY_TIMEZONE)
            created.append("company.timezone")
        else:
            skipped.append("company.timezone")

        for stmt in _COMPANY_INDEXES:
            try:
                conn.execute(stmt)
            except Exception as exc:
                # Table may not exist on a minimal install.
                skipped.append(f"{stmt.split('ON', 1)[-1].strip()}: {exc}")

        try:
            conn.execute(_CREATE_GL_TENANT_VIEW)
            created.append("v_gl_entry_tenant")
        except Exception as exc:
            skipped.append(f"v_gl_entry_tenant: {exc}")

        conn.commit()
        print("  pms_tenant + tenant indexes: applied.")
        return {"created": created, "skipped": skipped, "report_only": False}
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Migration 037: PMS tenant registry and company_id indexes")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()
    run_migration(args.db_path, report_only=args.report_only)
    print("erpclaw-setup migration 037 "
          + ("report complete (no writes)." if args.report_only else "complete."))
