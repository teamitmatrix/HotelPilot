"""PostgreSQL DDL adaptation keeps keys, foreign keys, and indexes."""
import os
import sys

import pytest

SETUP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(SETUP_DIR, "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from erpclaw_lib.pg_ddl import adapt_ddl_for_postgres


SAMPLE = """
CREATE TABLE IF NOT EXISTS example (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    is_system       INTEGER NOT NULL DEFAULT 0 CHECK(is_system IN (0,1)),
    allowed         INTEGER NOT NULL DEFAULT 1 CHECK(allowed IN (0,1)),
    debit           TEXT NOT NULL DEFAULT '0',
    old_values      TEXT,   -- JSON
    settings_json   TEXT,
    posting_date    TEXT NOT NULL,
    check_in        TEXT NOT NULL,
    created_at      TEXT DEFAULT (datetime('now')),
    locked_until    TEXT,
    company_id      TEXT NOT NULL REFERENCES company(id) ON DELETE RESTRICT,
    attempts        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_example_company ON example(company_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_example_name ON example(name);
"""


def test_sample_types_and_keys():
    sql = adapt_ddl_for_postgres(SAMPLE)
    assert "BIGSERIAL PRIMARY KEY" in sql
    assert "AUTOINCREMENT" not in sql.upper()
    assert "is_system SMALLINT NOT NULL DEFAULT 0 CHECK(is_system IN (0,1))" in sql
    assert "allowed SMALLINT NOT NULL DEFAULT 1 CHECK(allowed IN (0,1))" in sql
    assert "debit           TEXT NOT NULL DEFAULT '0'" in sql
    assert "old_values JSONB" in sql
    assert "settings_json JSONB" in sql
    assert "posting_date DATE NOT NULL" in sql
    assert "check_in DATE NOT NULL" in sql
    assert "created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP)" in sql
    assert "locked_until TIMESTAMP WITH TIME ZONE" in sql
    assert "datetime(" not in sql.lower()
    assert "REFERENCES company(id) ON DELETE RESTRICT" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_example_company" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_example_name" in sql
    assert "attempts        INTEGER NOT NULL DEFAULT 0" in sql
    assert "id TEXT" not in sql.split("name")[0] or "BIGSERIAL" in sql


def test_year_to_date_stays_text_amount():
    ddl = """
CREATE TABLE IF NOT EXISTS salary_structure (
    year_to_date    TEXT NOT NULL DEFAULT '0'
);
"""
    sql = adapt_ddl_for_postgres(ddl)
    assert "year_to_date    TEXT NOT NULL DEFAULT '0'" in sql
    assert "year_to_date DATE" not in sql


def test_multiline_bool_check_is_folded():
    ddl = """
CREATE TABLE IF NOT EXISTS tax_template_line (
    included_in_print_rate INTEGER NOT NULL DEFAULT 0
                    CHECK(included_in_print_rate IN (0,1)),
    is_tax_withholding_account INTEGER NOT NULL DEFAULT 0
                    CHECK(is_tax_withholding_account IN (0,1))
);
"""
    sql = adapt_ddl_for_postgres(ddl)
    assert "included_in_print_rate SMALLINT NOT NULL DEFAULT 0 CHECK(included_in_print_rate IN (0,1))" in sql
    assert "is_tax_withholding_account SMALLINT NOT NULL DEFAULT 0 CHECK(is_tax_withholding_account IN (0,1))" in sql


def test_uuid_primary_keys_and_constraints_are_preserved():
    if SETUP_DIR not in sys.path:
        sys.path.insert(0, SETUP_DIR)
    import init_schema

    original = "\n".join(ddl for _name, ddl in init_schema.ALL_DDL_BLOCKS)
    adapted = adapt_ddl_for_postgres(original)
    assert original.count("REFERENCES") == adapted.count("REFERENCES")
    assert original.count("PRIMARY KEY") == adapted.count("PRIMARY KEY")
    assert original.count("CREATE INDEX") == adapted.count("CREATE INDEX")
    assert original.count("CREATE UNIQUE INDEX") == adapted.count("CREATE UNIQUE INDEX")
    assert "id              TEXT PRIMARY KEY" in adapted
    assert "is_system SMALLINT NOT NULL DEFAULT 0 CHECK(is_system IN (0,1))" in adapted
    assert "created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP" in adapted
    assert "dimensions_json JSONB NOT NULL DEFAULT '{}'" in adapted
    assert "debit           TEXT NOT NULL DEFAULT '0'" in adapted
    assert "datetime('now')" not in adapted
    assert "is_system BOOLEAN" not in adapted
