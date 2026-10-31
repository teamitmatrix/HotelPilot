"""SQLite DDL adapted for PostgreSQL schema creation.

The checked-in CREATE TABLE statements stay valid SQLite. This module
rewrites a copy when ``DB_TYPE=postgres``:

* ``INTEGER PRIMARY KEY AUTOINCREMENT`` becomes ``BIGSERIAL PRIMARY KEY``.
  UUID primary keys stay ``TEXT`` so existing foreign keys still match.
* ``INTEGER`` flags with ``CHECK (col IN (0, 1))``, and ``is_*`` / ``has_*``
  / ``disabled`` / ``allowed`` integer flags, become ``SMALLINT`` (still
  0/1). They are not rewritten to ``BOOLEAN`` because PostgreSQL 16+ ships
  an explicit-only ``integer → boolean`` cast that cannot be replaced with
  an implicit one, so legacy ``0``/``1`` inserts would fail.
* ``*_at``, ``*_until``, ``timestamp``, and ``last_login`` ``TEXT`` columns
  become ``TIMESTAMP WITH TIME ZONE``.
* ``*_date`` and calendar columns (``check_in``, ``check_out``,
  ``business_date``, ``available_from``) become ``DATE``.
* ``*_json`` columns, and ``TEXT`` columns commented as JSON, become ``JSONB``.
* ``datetime('now')`` / ``date('now')`` become ``CURRENT_TIMESTAMP`` /
  ``CURRENT_DATE``.

``PRIMARY KEY``, ``REFERENCES``, ``UNIQUE``, and ``CREATE INDEX`` clauses
are left in place. PostgreSQL enforces those foreign keys and indexes
without a SQLite ``PRAGMA``.
"""
from __future__ import annotations

import re

_COL_LINE = re.compile(
    r"^(?P<indent>\s+)(?P<name>[A-Za-z_][\w]*)\s+"
    r"(?P<type>TEXT|INTEGER|INT|BIGINT|REAL|BLOB)\b(?P<rest>.*)$",
    re.IGNORECASE,
)
_BOOL_CHECK = re.compile(
    r"\s*CHECK\s*\(\s*(?P<col>[A-Za-z_][\w]*)\s+IN\s*\(\s*0\s*,\s*1\s*\)\s*\)",
    re.IGNORECASE,
)
# Continuation-only lines used in init_schema (CHECK on the line after the column).
_BOOL_CHECK_CONTINUATION = re.compile(
    r"^\s*CHECK\s*\(\s*[A-Za-z_][\w]*\s+IN\s*\(\s*0\s*,\s*1\s*\)\s*\)\s*,?\s*$",
    re.IGNORECASE,
)
_AUTOINCREMENT = re.compile(
    r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
    re.IGNORECASE,
)
_DATETIME_NOW = re.compile(r"datetime\(\s*'now'\s*\)", re.IGNORECASE)
_DATE_NOW = re.compile(r"date\(\s*'now'\s*\)", re.IGNORECASE)
_WITHOUT_ROWID = re.compile(r"\s+WITHOUT\s+ROWID\b", re.IGNORECASE)
_CREATE_VIEW_IF_NOT_EXISTS = re.compile(
    r"\bCREATE\s+VIEW\s+IF\s+NOT\s+EXISTS\b",
    re.IGNORECASE,
)
_JSON_COMMENT = re.compile(r"--\s*JSON\b", re.IGNORECASE)

_DATE_EXACT = frozenset({
    "check_in",
    "check_out",
    "available_from",
    "business_date",
})
_TS_EXACT = frozenset({
    "timestamp",
    "last_login",
    "last_active",
})
_FLAG_EXACT = frozenset({
    "disabled",
    "allowed",
    "active",
    "enabled",
})

# libpq type OIDs. Casters return the wire text so existing code that
# treats dates and JSON as strings keeps working against native columns.
_TEXT_OIDS = (
    (114, "JSON"),
    (3802, "JSONB"),
    (1082, "DATE"),
    (1114, "TIMESTAMP"),
    (1184, "TIMESTAMPTZ"),
)

BOOLEAN_INT_CAST_SQL = """
CREATE OR REPLACE FUNCTION erpclaw_int_to_bool(integer)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT CASE
    WHEN $1 IS NULL THEN NULL
    WHEN $1 = 0 THEN FALSE
    ELSE TRUE
  END
$$;

-- Prefer an IMPLICIT cast so legacy 0/1 inserts bind into BOOLEAN columns.
-- PostgreSQL 16+ already ships an *explicit* pg_catalog cast that cannot be
-- dropped; in that case skip CREATE CAST and rely on callers passing bools
-- (or on erpclaw_int_to_bool() for explicit casts in SQL).
DO $erpclaw_cast$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_cast
    WHERE castsource = 'integer'::regtype
      AND casttarget = 'boolean'::regtype
  ) THEN
    RETURN;
  END IF;

  CREATE CAST (integer AS boolean)
    WITH FUNCTION erpclaw_int_to_bool(integer)
    AS IMPLICIT;
END
$erpclaw_cast$;
"""


# Amount/balance columns that end in ``_date`` but are not calendar dates
# (e.g. payroll ``year_to_date`` stored as a decimal TEXT amount).
_DATE_NAME_EXCLUSIONS = frozenset({
    "year_to_date",
    "month_to_date",
    "quarter_to_date",
    "period_to_date",
})


def _is_date_name(name: str) -> bool:
    lowered = name.lower()
    if lowered in _DATE_NAME_EXCLUSIONS or lowered.endswith("_to_date"):
        return False
    return lowered in _DATE_EXACT or lowered.endswith("_date")


def _is_timestamp_name(name: str) -> bool:
    lowered = name.lower()
    return lowered in _TS_EXACT or lowered.endswith("_at") or lowered.endswith("_until")


def _is_flag_name(name: str) -> bool:
    lowered = name.lower()
    return lowered in _FLAG_EXACT or lowered.startswith("is_") or lowered.startswith("has_")


def _rewrite_column(line: str) -> str:
    line = _AUTOINCREMENT.sub("BIGSERIAL PRIMARY KEY", line)
    match = _COL_LINE.match(line)
    if not match:
        return line
    name = match.group("name")
    col_type = match.group("type")
    rest = match.group("rest")
    indent = match.group("indent")
    upper = col_type.upper()

    if upper in ("INTEGER", "INT", "BIGINT"):
        check = _BOOL_CHECK.search(rest)
        flag = check is not None and check.group("col").lower() == name.lower()
        if flag or (
            check is None
            and _is_flag_name(name)
            and re.search(r"\bDEFAULT\s+[01]\b", rest, re.I)
        ):
            # Keep 0/1 defaults and CHECKs; only narrow the storage type.
            return f"{indent}{name} SMALLINT{rest}"

    if upper == "TEXT":
        if name.lower().endswith("_json") or _JSON_COMMENT.search(rest):
            return f"{indent}{name} JSONB{rest}"
        if _is_timestamp_name(name):
            return f"{indent}{name} TIMESTAMP WITH TIME ZONE{rest}"
        if _is_date_name(name):
            return f"{indent}{name} DATE{rest}"
    return line


def _fold_bool_check_continuations(sql: str) -> str:
    """Attach ``CHECK(col IN (0,1))`` continuation lines to the prior column.

    Several ERP DDL blocks split the integer flag and its CHECK across two
    lines. Without folding, ``is_*`` columns become BOOLEAN on the first line
    while the leftover ``IN (0, 1)`` check fails on PostgreSQL.
    """
    lines = sql.splitlines()
    folded: list[str] = []
    for line in lines:
        if folded and _BOOL_CHECK_CONTINUATION.match(line):
            folded[-1] = folded[-1].rstrip() + " " + line.strip()
        else:
            folded.append(line)
    return "\n".join(folded)


def adapt_ddl_for_postgres(sql: str) -> str:
    """Return ``sql`` with PostgreSQL types substituted for SQLite spellings."""
    rewritten = _DATETIME_NOW.sub("CURRENT_TIMESTAMP", sql)
    rewritten = _DATE_NOW.sub("CURRENT_DATE", rewritten)
    rewritten = _WITHOUT_ROWID.sub("", rewritten)
    # PostgreSQL has no CREATE VIEW IF NOT EXISTS form. CREATE OR REPLACE is
    # the closest idempotent equivalent for SQLite-compatible schema scripts.
    rewritten = _CREATE_VIEW_IF_NOT_EXISTS.sub("CREATE OR REPLACE VIEW", rewritten)
    rewritten = _fold_bool_check_continuations(rewritten)
    return "\n".join(_rewrite_column(line) for line in rewritten.splitlines())


def install_boolean_int_cast(conn) -> None:
    """Allow existing inserts of 0/1 into native BOOLEAN columns."""
    cur = conn.cursor()
    try:
        cur.execute(BOOLEAN_INT_CAST_SQL)
        conn.commit()
    finally:
        cur.close()


def install_read_adapters(conn) -> None:
    """Return JSON and date values as text, matching the SQLite readers."""
    try:
        from psycopg2.extensions import new_type, register_type
    except ImportError:
        return

    def _as_text(value, _cursor):
        return value

    for oid, label in _TEXT_OIDS:
        register_type(new_type((oid,), label, _as_text), conn)
