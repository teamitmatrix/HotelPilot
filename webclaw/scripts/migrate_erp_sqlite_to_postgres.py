#!/usr/bin/env python3
"""One-time ERP / hotel migrate: SQLite → PostgreSQL schema ``public``.

Wraps ``erpclaw-engine/scripts/migrate_sqlite_to_postgres.py --only-erp``.

Typical cutover (after platform Step 1):

    python erpclaw-engine/scripts/init_postgres.py
    python webclaw/scripts/migrate_erp_sqlite_to_postgres.py --init-schema
    python erpclaw-engine/scripts/verify_sqlite_to_postgres.py --only-erp

Credentials: ``webclaw/api/.env`` (``DB_*`` / ``ERPCLAW_DB_URL``).
Source file: ``ERPCLAW_DB_PATH`` or ``data/data.sqlite``.
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATE = ROOT / "erpclaw-engine" / "scripts" / "migrate_sqlite_to_postgres.py"


def main() -> int:
    if not MIGRATE.is_file():
        print(f"ERROR: migration script not found: {MIGRATE}", file=sys.stderr)
        return 1
    extra = [a for a in sys.argv[1:] if a != "--only-erp"]
    sys.argv = [str(MIGRATE), "--only-erp", *extra]
    try:
        runpy.run_path(str(MIGRATE), run_name="__main__")
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
