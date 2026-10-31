# SQLite → PostgreSQL cutover (Step 7)

PMS runtime is **PostgreSQL only** (`DB_TYPE=postgres`).

| Schema | Contents |
|--------|----------|
| `webclaw` | Auth, sessions, plans, billing, audit, tickets, chat |
| `public` | ERP + hospitality (`company`, `pms_tenant`, rooms, reservations, …) |

## Completed cutover actions

1. **Verify** — `verify_sqlite_to_postgres.py` row counts / FK / app queries.
   Count mismatches where Postgres has *more* rows than SQLite are expected
   after the app has been writing to Postgres.
2. **Flip** — remove active `WEBCLAW_DB_PATH` / `ERPCLAW_DB_PATH` from
   `webclaw/api/.env` and `erpclaw-engine/.env`.
3. **Retain** — migrate + verify scripts stay in the repo; SQLite files moved
   to `data/sqlite_archive/<date>/` with a 30-day `RETENTION.md`.
4. **Delete** — after the retention window, remove the archive folder.

## Guardrails

```text
python webclaw/scripts/ci_grep_no_sqlite_runtime.py
```

## Manual checklist

- [ ] Login with a migrated user
- [ ] Open a hotel property; rooms/reservations load
- [ ] SuperAdmin roles/permissions list
- [ ] Create a reservation and confirm it persists
- [ ] Night-audit / status endpoints return 200
- [ ] Day-to-day API needs no SQLite file locks
