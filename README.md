# HotelPilot

HotelPilot is a multi-tenant hotel property management system. Staff run the front desk, rooms, rates, and night audit in the browser. Accounting stays in the ERP engine, and each hotel’s rows are isolated by tenant.

## What it includes

- **Front desk** — reservations, room assignment, check-in, check-out, and folios
- **Housekeeping** — room status (clean, dirty, maintenance)
- **Inventory and rates** — availability, restrictions, allotments, rate plans, and dynamic pricing
- **Channels** — channel connections, sync status, production, and commission
- **Night audit and reports** — arrivals, in-house, departures, occupancy, flash, cashier, ledger, and forecast
- **Billing** — folios and optional Stripe card payments
- **Guest portal and messaging** — guest access, with optional email and SMS providers
- **Access control** — JWT sessions and role checks for hotel staff and platform admin

## Layout

| Path | Role |
|------|------|
| `webclaw/web` | Next.js staff and guest UI |
| `webclaw/api` | FastAPI backend |
| `hospitalityclaw` | Hotel domain: rooms, guests, reservations, folios |
| `erpclaw-engine` | Accounting and ERP engine |
| `data` | Local data directory (databases are not in git) |
| `docs` | Operational notes, including the PostgreSQL cutover |

`webclaw` and `erpclaw-engine` are based on the GPL-3.0 projects [webclaw](https://github.com/avansaber/webclaw) and [erpclaw](https://github.com/avansaber/erpclaw). Their `LICENSE.txt` files stay in those folders.

## Requirements

- Python 3.10+
- Node.js 20+
- PostgreSQL 18 (local development uses database `pms`)

Runtime data is PostgreSQL only. SQLite files are for one-off migration and verification, not for the running app.

## Local setup

From this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r webclaw\api\requirements.txt -r erpclaw-engine\requirements.txt -r hospitalityclaw\requirements.txt

copy webclaw\api\.env.example webclaw\api\.env
copy erpclaw-engine\.env.example erpclaw-engine\.env
copy webclaw\web\.env.local.example webclaw\web\.env.local
```

Put the database password and any API keys only in those local env files. They are gitignored. The examples list every setting, including optional Stripe, LLM, SendGrid, Twilio, and SMTP values.

Create the database, then start the API and the web app in two terminals:

```powershell
$env:DB_NAME = "pms"
python erpclaw-engine\scripts\init_postgres.py

# Terminal 1 — API (from webclaw/)
cd webclaw
..\.venv\Scripts\uvicorn api.main:app --reload --host 127.0.0.1 --port 8001

# Terminal 2 — web (from webclaw/web/)
cd webclaw\web
npm install
npm run dev
```

Point `NEXT_PUBLIC_API_URL` in `webclaw/web/.env.local` at the API (the example uses port 8000; `api/main.py` listens on 8001 unless you change it).

Open the web app and complete first-time setup to create an admin account.

## Tests

```powershell
cd webclaw\api
..\..\.venv\Scripts\pytest

cd ..\web
npm test
```
