# SQLite retention (Step 7 cutover)

Archived on **2026-10-12** after Postgres verify showed:

- Platform (`webclaw`): all tables matched except `audit_log`
  (Postgres had **more** rows — post-cutover writes).
- ERP (`public`): 226/238 tables matched; mismatches are tables where
  Postgres had **more** rows than SQLite (app continued writing to PG).
- No missing-with-data tables; FK orphan scans passed.

## Files

| File | Former path |
|------|-------------|
| `webclaw.sqlite` | `data/webclaw.sqlite` |
| `data.sqlite` | `data/data.sqlite` |

## Retention window

Keep these archives until **2026-11-12** (30 days). After that date, if
Postgres remains healthy, delete this folder.

Do **not** point `WEBCLAW_DB_PATH` / `ERPCLAW_DB_PATH` at these files for
day-to-day runtime — the API requires `DB_TYPE=postgres`.

## Re-verify (optional)

```text
# Temporarily set paths to these archives, then:
python erpclaw-engine/scripts/verify_sqlite_to_postgres.py --only-webclaw
python erpclaw-engine/scripts/verify_sqlite_to_postgres.py --only-erp
```

Migrate tooling (retained in repo):

- `webclaw/scripts/migrate_platform_sqlite_to_postgres.py`
- `webclaw/scripts/migrate_erp_sqlite_to_postgres.py`
- `erpclaw-engine/scripts/migrate_sqlite_to_postgres.py`
- `erpclaw-engine/scripts/verify_sqlite_to_postgres.py`
