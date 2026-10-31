"""Inject a tenant predicate into DML/SELECT so hotel rows cannot leak.

Canonical tenant key is ``company.id``. Tables store it as:

  * ``tenant_id`` (hospitality / PMS)
  * ``company_id`` (ERP operational tables)
  * ``id`` on ``company`` itself

Callers supply ``column_for(table) -> column | None``. This module only
rewrites SQL; it never interpolates the UUID into the statement.

Sequence params keep qmark ``?`` placeholders (SQLite cannot mix qmark
with named). Mapping params use ``:current_tenant_id``.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Mapping, Sequence

ColumnFor = Callable[[str], str | None]

_SKIP_PREFIX = re.compile(
    r"^\s*(?:/\*.*?\*/\s*)?(?:pragma|begin|commit|rollback|savepoint|release|"
    r"create|drop|alter|attach|detach|vacuum|analyze|reindex|explain|"
    r"replace\s+into)\b",
    re.I | re.S,
)

_LEAD = re.compile(r"^\s*(select|update|delete|insert)\b", re.I)

_TAIL = re.compile(
    r"\b(?:order\s+by|group\s+by|having|limit|offset|returning|on\s+conflict)\b",
    re.I,
)

_NOT_ALIAS = frozenset({
    "where", "set", "join", "left", "right", "inner", "outer", "cross",
    "on", "order", "group", "limit", "offset", "values", "select",
    "natural", "using", "as", "returning", "of", "only", "into",
    "from", "and", "or", "union", "except", "intersect",
})

_IDENT = r'(?:["`\[])?(?P<table>[A-Za-z_][\w]*)(?:["`\]])?'
_ALIAS = r'(?:\s+(?:as\s+)?(?P<alias>[A-Za-z_][\w]*))?'


def _column_names(raw_conn, table: str) -> set[str]:
    """Return table columns on SQLite or PostgreSQL without aborting a PG tx."""
    try:
        from db_access import table_columns

        return table_columns(raw_conn, table)
    except Exception:
        return set()


def sqlite_tenant_column(raw_conn, table: str) -> str | None:
    """Read the tenant column for ``table`` on SQLite or PostgreSQL."""
    if not re.match(r"^[A-Za-z_][\w]*$", table):
        return None
    if table.lower() in ("sqlite_master", "sqlite_sequence", "sqlite_temp_master"):
        return None
    cols = _column_names(raw_conn, table)
    if "tenant_id" in cols:
        return "tenant_id"
    if "company_id" in cols:
        return "company_id"
    if table == "company" and "id" in cols:
        return "id"
    return None


def apply_tenant_predicate(
    sql: str,
    params: Sequence[Any] | Mapping[str, Any] | None,
    tenant_id: str,
    column_for: ColumnFor,
) -> tuple[str, Sequence[Any] | Mapping[str, Any] | None]:
    """Return ``(sql, params)`` with a tenant predicate on the primary table.

    No-ops for DDL, PRAGMA, unscoped tables, and statements whose primary
    relation is a subquery.
    """
    if not tenant_id or not sql or not sql.strip():
        return sql, params
    if _SKIP_PREFIX.match(sql):
        return sql, params

    lead = _LEAD.match(sql)
    if not lead:
        return sql, params
    kind = lead.group(1).lower()

    if kind == "insert":
        return _scope_insert(sql, params, tenant_id, column_for)

    parsed = _primary_table(sql, kind)
    if not parsed:
        return sql, params
    table, alias = parsed
    column = column_for(table)
    if not column:
        return sql, params
    qual = alias or table
    predicate = f"{qual}.{column} = " + (
        ":current_tenant_id" if isinstance(params, Mapping) else "?"
    )
    return _inject_where(sql, params, tenant_id, predicate)


def apply_multi_property_predicate(
    sql: str,
    params: Sequence[Any] | Mapping[str, Any] | None,
    property_ids: Sequence[str],
    column_for: ColumnFor,
    *,
    group_id: str | None = None,
    has_group_column: Callable[[str], bool] | None = None,
) -> tuple[str, Sequence[Any] | Mapping[str, Any] | None]:
    """Scope SELECT/UPDATE/DELETE across many properties (group ALL mode).

    Prefer ``group_id = :current_group_id`` when the table has that column;
    otherwise ``tenant_id|company_id IN (...)``. INSERT is left untouched —
    callers must block writes under aggregation.
    """
    ids = [str(p).strip() for p in property_ids if p and str(p).strip()]
    if (not ids and not group_id) or not sql or not sql.strip():
        return sql, params
    if _SKIP_PREFIX.match(sql):
        return sql, params

    lead = _LEAD.match(sql)
    if not lead:
        return sql, params
    kind = lead.group(1).lower()
    if kind == "insert":
        return sql, params

    parsed = _primary_table(sql, kind)
    if not parsed:
        return sql, params
    table, alias = parsed
    qual = alias or table

    if group_id and has_group_column and has_group_column(table):
        predicate = f"{qual}.group_id = " + (
            ":current_group_id" if isinstance(params, Mapping) else "?"
        )
        return _inject_where(sql, params, group_id, predicate)

    column = column_for(table)
    if not column or not ids:
        return sql, params

    if isinstance(params, Mapping):
        placeholders = ", ".join(f":prop_id_{i}" for i in range(len(ids)))
        predicate = f"{qual}.{column} IN ({placeholders})"
        at = _first_tail(sql)
        clause = f" AND {predicate}" if _has_where(sql, at) else f" WHERE {predicate}"
        head, tail = sql[:at], sql[at:]
        new_sql = (
            f"{head.rstrip()}{clause} {tail.lstrip()}" if tail.strip() else f"{head.rstrip()}{clause}"
        )
        bound = dict(params)
        for i, pid in enumerate(ids):
            bound[f"prop_id_{i}"] = pid
        return new_sql, bound

    placeholders = ", ".join("?" for _ in ids)
    predicate = f"{qual}.{column} IN ({placeholders})"
    at = _first_tail(sql)
    clause = f" AND {predicate}" if _has_where(sql, at) else f" WHERE {predicate}"
    head, tail = sql[:at], sql[at:]
    new_sql = (
        f"{head.rstrip()}{clause} {tail.lstrip()}" if tail.strip() else f"{head.rstrip()}{clause}"
    )
    if params is None:
        return new_sql, tuple(ids)
    seq = list(params)
    idx = _qmark_count(head)
    for offset, pid in enumerate(ids):
        seq.insert(idx + offset, pid)
    return new_sql, tuple(seq) if isinstance(params, tuple) else seq


def sqlite_has_group_column(raw_conn, table: str) -> bool:
    """True when ``table`` has a ``group_id`` column on either backend."""
    if not re.match(r"^[A-Za-z_][\w]*$", table):
        return False
    return "group_id" in _column_names(raw_conn, table)


def _primary_table(sql: str, kind: str) -> tuple[str, str | None] | None:
    if kind == "select":
        from_paren = re.search(r"\bfrom\s*\(", sql, re.I)
        from_ident = re.search(rf"\bfrom\s+{_IDENT}{_ALIAS}", sql, re.I)
        if not from_ident:
            return None
        if from_paren and from_paren.start() < from_ident.start():
            return None
        return _table_alias(from_ident)
    if kind == "update":
        m = re.search(rf"^\s*update\s+{_IDENT}{_ALIAS}", sql, re.I)
        return _table_alias(m) if m else None
    if kind == "delete":
        m = re.search(rf"^\s*delete\s+from\s+{_IDENT}{_ALIAS}", sql, re.I)
        return _table_alias(m) if m else None
    return None


def _table_alias(match: re.Match) -> tuple[str, str | None]:
    table = match.group("table")
    alias = match.groupdict().get("alias")
    if alias and alias.lower() in _NOT_ALIAS:
        alias = None
    return table, alias


def _outside_strings(sql: str) -> list[bool]:
    """True at each index if that character is outside a SQL string literal."""
    flags = [True] * len(sql)
    in_str = False
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            if in_str and i + 1 < n and sql[i + 1] == "'":
                flags[i] = False
                flags[i + 1] = False
                i += 2
                continue
            in_str = not in_str
            flags[i] = False
        else:
            flags[i] = not in_str
        i += 1
    return flags


def _first_tail(sql: str) -> int:
    outside = _outside_strings(sql)
    for m in _TAIL.finditer(sql):
        if outside[m.start()]:
            return m.start()
    stripped = sql.rstrip()
    if stripped.endswith(";"):
        return len(stripped) - 1
    return len(sql)


def _has_where(sql: str, end: int) -> bool:
    outside = _outside_strings(sql)
    for m in re.finditer(r"\bwhere\b", sql[:end], re.I):
        if outside[m.start()]:
            return True
    return False


def _qmark_count(sql: str) -> int:
    outside = _outside_strings(sql)
    return sum(1 for i, ch in enumerate(sql) if ch == "?" and outside[i])


def _inject_where(sql, params, tenant_id, predicate):
    at = _first_tail(sql)
    clause = f" AND {predicate}" if _has_where(sql, at) else f" WHERE {predicate}"
    head, tail = sql[:at], sql[at:]
    new_sql = f"{head.rstrip()}{clause} {tail.lstrip()}" if tail.strip() else f"{head.rstrip()}{clause}"

    if params is None:
        return new_sql, (tenant_id,)
    if isinstance(params, Mapping):
        bound = dict(params)
        bound["current_tenant_id"] = tenant_id
        return new_sql, bound

    seq = list(params)
    idx = _qmark_count(head)
    seq.insert(idx, tenant_id)
    return new_sql, tuple(seq) if isinstance(params, tuple) else seq


def _scope_insert(sql, params, tenant_id, column_for):
    m = re.match(
        rf"^\s*insert\s+into\s+{_IDENT}\s*\((?P<cols>[^)]+)\)\s*values\s*\(",
        sql,
        re.I,
    )
    if not m:
        return sql, params
    table = m.group("table")
    column = column_for(table)
    if not column:
        return sql, params
    cols = [c.strip().strip('"').strip("`").strip("[]") for c in m.group("cols").split(",")]
    if column in cols or column.lower() in {c.lower() for c in cols}:
        return sql, params

    # Insert the tenant column and a matching placeholder before the closing
    # paren of the first VALUES tuple.
    col_sql = sql[: m.end("cols")] + f", {column}" + sql[m.end("cols") :]
    # Find the VALUES '(' we already matched — append ? before its closing ')'.
    values_open = col_sql.upper().rfind("VALUES", 0, len(col_sql))
    # Search from original match end, adjusted for the inserted column name.
    # Simpler: find last ')' of the statement's first tuple at top level.
    close = _first_top_level_close_paren(col_sql, col_sql.upper().find("VALUES"))
    if close < 0:
        return sql, params
    placeholder = ":current_tenant_id" if isinstance(params, Mapping) else "?"
    new_sql = col_sql[:close] + f", {placeholder}" + col_sql[close:]

    if params is None:
        return new_sql, (tenant_id,)
    if isinstance(params, Mapping):
        bound = dict(params)
        bound["current_tenant_id"] = tenant_id
        return new_sql, bound
    seq = list(params)
    seq.append(tenant_id)
    return new_sql, tuple(seq) if isinstance(params, tuple) else seq


def _first_top_level_close_paren(sql: str, start: int) -> int:
    outside = _outside_strings(sql)
    depth = 0
    i = start
    n = len(sql)
    while i < n:
        if not outside[i]:
            i += 1
            continue
        if sql[i] == "(":
            depth += 1
        elif sql[i] == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1
