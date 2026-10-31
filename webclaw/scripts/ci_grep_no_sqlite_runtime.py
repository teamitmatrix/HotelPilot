#!/usr/bin/env python3
"""Fail if SQLite runtime sneaks back into product code (migration step 6).

Migrate/verify scripts and historical ERP migrations are excluded — those
remain until Step 7 (data-file cutover).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOW_SQLITE_CONNECT = {
    "erpclaw-engine/scripts/migrate_sqlite_to_postgres.py",
    "erpclaw-engine/scripts/verify_sqlite_to_postgres.py",
    "webclaw/scripts/migrate_platform_sqlite_to_postgres.py",
    "webclaw/scripts/migrate_erp_sqlite_to_postgres.py",
    "webclaw/scripts/ci_grep_no_sqlite_runtime.py",
}
SKIP_DIR_NAMES = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    "vendor",
    "migrations",
    "tests",
}

CONNECT_RE = re.compile(r"sqlite3\.connect\s*\(")
ALLOW_RE = re.compile(r"WEBCLAW_ALLOW_SQLITE")
IMPORT_RE = re.compile(r"^(?:import sqlite3|from sqlite3 )", re.M)


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _iter_py_files() -> list[Path]:
    files: list[Path] = []
    for base in (
        ROOT / "webclaw" / "api",
        ROOT / "webclaw" / "scripts",
        ROOT / "erpclaw-engine" / "scripts" / "erpclaw-setup" / "lib" / "erpclaw_lib",
    ):
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if any(part in SKIP_DIR_NAMES for part in path.parts):
                continue
            files.append(path)
    return files


def main() -> int:
    failures: list[str] = []
    for path in _iter_py_files():
        rel = _rel(path)
        text = path.read_text(encoding="utf-8")
        if rel in ALLOW_SQLITE_CONNECT:
            continue
        if ALLOW_RE.search(text) and "conftest.py" not in rel:
            failures.append(f"{rel}: WEBCLAW_ALLOW_SQLITE must not appear")
        if CONNECT_RE.search(text):
            failures.append(f"{rel}: sqlite3.connect is not allowed at runtime")
        if rel.startswith("webclaw/api/") and IMPORT_RE.search(text):
            failures.append(f"{rel}: import sqlite3 is not allowed in the API")
        if rel.endswith("erpclaw_lib/db.py") and CONNECT_RE.search(text):
            failures.append(f"{rel}: sqlite3.connect must stay out of get_connection")

    if failures:
        print("SQLite runtime leaked back into product code:", file=sys.stderr)
        for item in failures:
            print(f"  {item}", file=sys.stderr)
        return 1
    print("ok: no SQLite runtime in scanned product paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
