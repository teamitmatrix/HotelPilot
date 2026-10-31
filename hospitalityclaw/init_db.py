"""HospitalityClaw schema — hotel PMS tables, all row-scoped by tenant_id.

tenant_id == company.id (the hotel's ERP company UUID). Money is TEXT.
"""

from __future__ import annotations


def _is_postgres(conn) -> bool:
    """True when ``conn`` is a Postgres facade / psycopg2 connection.

    Prefer the concrete connection module over the process-wide dialect: unit
    tests use in-memory SQLite even when the workspace ``.env`` points at
    Postgres, and treating those connections as Postgres rolls back DDL.
    """
    raw = getattr(conn, "_conn", conn)
    mod = type(raw).__module__
    if "psycopg" in mod:
        return True
    if "sqlite3" in mod or type(raw).__name__ == "Connection" and hasattr(raw, "executescript"):
        # Plain sqlite3.Connection (and thin test wrappers) are never Postgres.
        try:
            raw.execute("PRAGMA foreign_keys")
            return False
        except Exception:
            pass
    try:
        from db_access import get_dialect

        return get_dialect() == "postgresql"
    except Exception:
        return False


def _table_columns(conn, table: str) -> set[str]:
    """Column names for ``table`` on SQLite or PostgreSQL."""
    if _is_postgres(conn):
        try:
            rows = conn.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = ?
                """,
                (table,),
            ).fetchall()
            return {r[0] for r in rows}
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return set()
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        if rows:
            return {r[1] for r in rows}
    except Exception:
        pass
    return set()


def _exec_script(conn, script: str) -> None:
    """Run a multi-statement DDL script on SQLite or PostgreSQL."""
    executescript = getattr(conn, "executescript", None)
    if callable(executescript) and not _is_postgres(conn):
        executescript(script)
        return
    for stmt in _split_sql_statements(script):
        _safe_exec(conn, stmt)


HOSPITALITY_DDL = """
CREATE TABLE IF NOT EXISTS hospitalityclaw_hotel (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES company(id) ON DELETE RESTRICT,
    name            TEXT NOT NULL,
    timezone        TEXT NOT NULL DEFAULT 'UTC',
    currency        TEXT NOT NULL DEFAULT 'USD',
    country         TEXT NOT NULL DEFAULT 'United States',
    star_rating     INTEGER,
    address         TEXT,
    phone           TEXT,
    email           TEXT,
    settings_json   TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_hclaw_hotel_tenant ON hospitalityclaw_hotel(tenant_id);

CREATE TABLE IF NOT EXISTS hospitalityclaw_room_type (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES company(id) ON DELETE RESTRICT,
    hotel_id        TEXT NOT NULL REFERENCES hospitalityclaw_hotel(id) ON DELETE RESTRICT,
    name            TEXT NOT NULL,
    code            TEXT NOT NULL,
    base_rate       TEXT NOT NULL DEFAULT '0',
    max_occupancy   INTEGER NOT NULL DEFAULT 2,
    description     TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, hotel_id, code)
);
CREATE INDEX IF NOT EXISTS idx_hclaw_room_type_tenant ON hospitalityclaw_room_type(tenant_id);
CREATE INDEX IF NOT EXISTS idx_hclaw_room_type_hotel ON hospitalityclaw_room_type(hotel_id);

CREATE TABLE IF NOT EXISTS hospitalityclaw_room (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES company(id) ON DELETE RESTRICT,
    hotel_id        TEXT NOT NULL REFERENCES hospitalityclaw_hotel(id) ON DELETE RESTRICT,
    room_type_id    TEXT NOT NULL REFERENCES hospitalityclaw_room_type(id) ON DELETE RESTRICT,
    room_number     TEXT NOT NULL,
    floor           TEXT,
    status          TEXT NOT NULL DEFAULT 'vacant'
                    CHECK(status IN ('vacant','occupied','dirty','out_of_order')),
    -- Earliest check-in date after dirty cleaning (ISO YYYY-MM-DD).
    -- Unused for maintenance: out_of_order rooms stay off inventory until clean.
    available_from  TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, hotel_id, room_number)
);
CREATE INDEX IF NOT EXISTS idx_hclaw_room_tenant ON hospitalityclaw_room(tenant_id);
CREATE INDEX IF NOT EXISTS idx_hclaw_room_hotel ON hospitalityclaw_room(hotel_id);
CREATE INDEX IF NOT EXISTS idx_hclaw_room_status ON hospitalityclaw_room(tenant_id, status);

CREATE TABLE IF NOT EXISTS hospitalityclaw_guest (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES company(id) ON DELETE RESTRICT,
    first_name      TEXT NOT NULL,
    last_name       TEXT NOT NULL,
    email           TEXT,
    phone           TEXT,
    nationality     TEXT,
    id_number       TEXT,
    notes           TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_hclaw_guest_tenant ON hospitalityclaw_guest(tenant_id);
CREATE INDEX IF NOT EXISTS idx_hclaw_guest_email ON hospitalityclaw_guest(tenant_id, email);

CREATE TABLE IF NOT EXISTS hospitalityclaw_reservation (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES company(id) ON DELETE RESTRICT,
    hotel_id        TEXT NOT NULL REFERENCES hospitalityclaw_hotel(id) ON DELETE RESTRICT,
    guest_id        TEXT NOT NULL REFERENCES hospitalityclaw_guest(id) ON DELETE RESTRICT,
    room_id         TEXT REFERENCES hospitalityclaw_room(id) ON DELETE RESTRICT,
    room_type_id    TEXT NOT NULL REFERENCES hospitalityclaw_room_type(id) ON DELETE RESTRICT,
    check_in        TEXT NOT NULL,
    check_out       TEXT NOT NULL,
    adults          INTEGER NOT NULL DEFAULT 1,
    children        INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'reserved'
                    CHECK(status IN ('reserved','pre_checked_in','checked_in','checked_out','cancelled','no_show')),
    rate            TEXT NOT NULL DEFAULT '0',
    notes           TEXT,
    booking_token   TEXT UNIQUE,
    pre_check_in_at TEXT,
    registration_signed_at TEXT,
    checkout_requested_at TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_hclaw_res_tenant ON hospitalityclaw_reservation(tenant_id);
CREATE INDEX IF NOT EXISTS idx_hclaw_res_dates ON hospitalityclaw_reservation(tenant_id, check_in, check_out);
CREATE INDEX IF NOT EXISTS idx_hclaw_res_status ON hospitalityclaw_reservation(tenant_id, status);
-- idx_hclaw_res_token is created in _migrate_reservation_guest_portal after
-- booking_token exists on upgraded databases (CREATE TABLE IF NOT EXISTS is a no-op).

CREATE TABLE IF NOT EXISTS hospitalityclaw_guest_document (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES company(id) ON DELETE RESTRICT,
    guest_id        TEXT NOT NULL REFERENCES hospitalityclaw_guest(id) ON DELETE CASCADE,
    reservation_id  TEXT REFERENCES hospitalityclaw_reservation(id) ON DELETE CASCADE,
    doc_type        TEXT NOT NULL DEFAULT 'id_photo'
                    CHECK(doc_type IN ('id_photo','passport','registration_signature','other')),
    file_name       TEXT,
    mime_type       TEXT,
    storage_path    TEXT NOT NULL,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_hclaw_guest_doc_tenant ON hospitalityclaw_guest_document(tenant_id);
CREATE INDEX IF NOT EXISTS idx_hclaw_guest_doc_res ON hospitalityclaw_guest_document(reservation_id);

CREATE TABLE IF NOT EXISTS hospitalityclaw_folio (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES company(id) ON DELETE RESTRICT,
    reservation_id  TEXT NOT NULL REFERENCES hospitalityclaw_reservation(id) ON DELETE RESTRICT,
    guest_id        TEXT NOT NULL REFERENCES hospitalityclaw_guest(id) ON DELETE RESTRICT,
    status          TEXT NOT NULL DEFAULT 'open'
                    CHECK(status IN ('open','closed','cancelled')),
    balance         TEXT NOT NULL DEFAULT '0',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, reservation_id)
);
CREATE INDEX IF NOT EXISTS idx_hclaw_folio_tenant ON hospitalityclaw_folio(tenant_id);

CREATE TABLE IF NOT EXISTS hospitalityclaw_folio_charge (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES company(id) ON DELETE RESTRICT,
    folio_id        TEXT NOT NULL REFERENCES hospitalityclaw_folio(id) ON DELETE RESTRICT,
    description     TEXT NOT NULL,
    amount          TEXT NOT NULL DEFAULT '0',
    charge_type     TEXT NOT NULL DEFAULT 'room'
                    CHECK(charge_type IN ('room','tax','fnb','other','payment')),
    posting_date    TEXT NOT NULL,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_hclaw_charge_tenant ON hospitalityclaw_folio_charge(tenant_id);
CREATE INDEX IF NOT EXISTS idx_hclaw_charge_folio ON hospitalityclaw_folio_charge(folio_id);

CREATE TABLE IF NOT EXISTS hospitalityclaw_ledger_entry (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES company(id) ON DELETE RESTRICT,
    folio_id        TEXT NOT NULL REFERENCES hospitalityclaw_folio(id) ON DELETE RESTRICT,
    debit           TEXT NOT NULL DEFAULT '0',
    credit          TEXT NOT NULL DEFAULT '0',
    account_code    TEXT,
    description     TEXT NOT NULL,
    posting_date    TEXT NOT NULL,
    source_charge_id TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_hclaw_ledger_tenant ON hospitalityclaw_ledger_entry(tenant_id);
CREATE INDEX IF NOT EXISTS idx_hclaw_ledger_folio ON hospitalityclaw_ledger_entry(folio_id);

CREATE TABLE IF NOT EXISTS hospitalityclaw_transaction (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES company(id) ON DELETE RESTRICT,
    folio_id        TEXT NOT NULL REFERENCES hospitalityclaw_folio(id) ON DELETE RESTRICT,
    amount          TEXT NOT NULL DEFAULT '0',
    payment_method  TEXT NOT NULL DEFAULT 'cash'
                    CHECK(payment_method IN ('cash','card','bank','other')),
    status          TEXT NOT NULL DEFAULT 'posted'
                    CHECK(status IN ('draft','posted','void')),
    posted_at       TEXT,
    reference       TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_hclaw_txn_tenant ON hospitalityclaw_transaction(tenant_id);
CREATE INDEX IF NOT EXISTS idx_hclaw_txn_folio ON hospitalityclaw_transaction(folio_id);

CREATE TABLE IF NOT EXISTS hospitalityclaw_rate_plan (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES company(id) ON DELETE RESTRICT,
    hotel_id        TEXT NOT NULL REFERENCES hospitalityclaw_hotel(id) ON DELETE RESTRICT,
    room_type_id    TEXT REFERENCES hospitalityclaw_room_type(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    code            TEXT NOT NULL,
    start_date      TEXT NOT NULL,
    end_date        TEXT NOT NULL,
    nightly_rate    TEXT NOT NULL DEFAULT '0',
    priority        INTEGER NOT NULL DEFAULT 0,
    is_active       INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
    notes           TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, hotel_id, code)
);
CREATE INDEX IF NOT EXISTS idx_hclaw_rate_plan_tenant ON hospitalityclaw_rate_plan(tenant_id);
CREATE INDEX IF NOT EXISTS idx_hclaw_rate_plan_dates
    ON hospitalityclaw_rate_plan(tenant_id, hotel_id, start_date, end_date);

CREATE TABLE IF NOT EXISTS hospitalityclaw_tax_rule (
    id                  TEXT PRIMARY KEY,
    tenant_id           TEXT NOT NULL REFERENCES company(id) ON DELETE RESTRICT,
    hotel_id            TEXT NOT NULL REFERENCES hospitalityclaw_hotel(id) ON DELETE RESTRICT,
    name                TEXT NOT NULL,
    rate_percent        TEXT NOT NULL DEFAULT '0',
    applies_to          TEXT NOT NULL DEFAULT 'room'
                        CHECK(applies_to IN ('room','fnb','all')),
    erp_tax_template_id TEXT,
    is_default          INTEGER NOT NULL DEFAULT 0 CHECK(is_default IN (0,1)),
    is_active           INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, hotel_id, name)
);
CREATE INDEX IF NOT EXISTS idx_hclaw_tax_rule_tenant ON hospitalityclaw_tax_rule(tenant_id);
CREATE INDEX IF NOT EXISTS idx_hclaw_tax_rule_hotel ON hospitalityclaw_tax_rule(hotel_id);
"""


def _safe_exec(conn, stmt: str) -> None:
    """Execute one DDL/DML statement; roll back aborted Postgres txs on error."""
    try:
        conn.execute(stmt)
    except Exception as exc:
        if _is_postgres(conn):
            try:
                conn.rollback()
            except Exception:
                pass
        msg = str(exc).lower()
        if (
            "no such column" in msg
            or "duplicate column" in msg
            or "already exists" in msg
            or "duplicate key" in msg
        ):
            return
        raise


def init_db(conn) -> None:
    """Create hospitality tables. Idempotent. Shared ERP database."""
    # On PostgreSQL the full schema is created by init_postgres.py. Re-running
    # the SQLite-shaped HOSPITALITY_DDL is unnecessary and can abort the
    # transaction; only apply additive migrations.
    if not (_is_postgres(conn) and _table_columns(conn, "hospitalityclaw_hotel")):
        for stmt in _split_sql_statements(HOSPITALITY_DDL):
            try:
                conn.execute(stmt)
            except Exception as exc:
                if _is_postgres(conn):
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                msg = str(exc).lower()
                # Older reservation tables lack booking_token until migration below.
                if "no such column" in msg or "duplicate column" in msg:
                    continue
                if "already exists" in msg:
                    continue
                raise
    cols = _table_columns(conn, "hospitalityclaw_hotel")
    if cols and "settings_json" not in cols:
        _safe_exec(conn, "ALTER TABLE hospitalityclaw_hotel ADD COLUMN settings_json TEXT")
    room_cols = _table_columns(conn, "hospitalityclaw_room")
    if room_cols and "available_from" not in room_cols:
        _safe_exec(conn, "ALTER TABLE hospitalityclaw_room ADD COLUMN available_from TEXT")
    _migrate_folio_status_cancelled(conn)
    _backfill_cancelled_folios(conn)
    _migrate_reservation_guest_portal(conn)
    _migrate_night_audit_tables(conn)
    _migrate_ledger_source(conn)
    conn.commit()


def _split_sql_statements(script: str) -> list[str]:
    """Split a SQL script into executable statements (semicolon-delimited)."""
    parts: list[str] = []
    buf: list[str] = []
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        buf.append(line)
        if ";" in line:
            chunk = "\n".join(buf).strip()
            buf = []
            for piece in chunk.split(";"):
                piece = piece.strip()
                if piece:
                    parts.append(piece)
    tail = "\n".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


NIGHT_AUDIT_DDL = """
        CREATE TABLE IF NOT EXISTS hospitalityclaw_night_audit_run (
            id              TEXT PRIMARY KEY,
            tenant_id       TEXT NOT NULL,
            hotel_id        TEXT NOT NULL,
            business_date   TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'completed'
                            CHECK(status IN ('running','completed','failed')),
            triggered_by    TEXT,
            summary_json    TEXT,
            started_at      TEXT,
            completed_at    TEXT,
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_hclaw_night_audit_tenant
            ON hospitalityclaw_night_audit_run(tenant_id, business_date);
        CREATE INDEX IF NOT EXISTS idx_hclaw_night_audit_hotel
            ON hospitalityclaw_night_audit_run(hotel_id, business_date);

        CREATE TABLE IF NOT EXISTS hospitalityclaw_cash_drawer_day (
            id                  TEXT PRIMARY KEY,
            tenant_id           TEXT NOT NULL,
            hotel_id            TEXT NOT NULL,
            business_date       TEXT NOT NULL,
            opening_balance     TEXT NOT NULL DEFAULT '0',
            cash_received       TEXT NOT NULL DEFAULT '0',
            card_received       TEXT NOT NULL DEFAULT '0',
            bank_received       TEXT NOT NULL DEFAULT '0',
            other_received      TEXT NOT NULL DEFAULT '0',
            expected_cash       TEXT NOT NULL DEFAULT '0',
            closing_balance     TEXT NOT NULL DEFAULT '0',
            variance            TEXT NOT NULL DEFAULT '0',
            status              TEXT NOT NULL DEFAULT 'open'
                                CHECK(status IN ('open','closed')),
            closed_at           TEXT,
            closed_by           TEXT,
            night_audit_run_id  TEXT,
            notes               TEXT,
            created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at          TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(tenant_id, hotel_id, business_date)
        );
        CREATE INDEX IF NOT EXISTS idx_hclaw_cash_drawer_tenant
            ON hospitalityclaw_cash_drawer_day(tenant_id, business_date);
"""


def _migrate_night_audit_tables(conn) -> None:
    """Night audit run log + cash drawer day close (shared ERP/hospitality DB)."""
    _exec_script(conn, NIGHT_AUDIT_DDL)


def _migrate_reservation_guest_portal(conn) -> None:
    """Add booking_token / pre_checked_in / portal columns + guest documents."""
    # Ensure guest_document table exists even on older DBs that already had DDL run.
    _exec_script(
        conn,
        """
        CREATE TABLE IF NOT EXISTS hospitalityclaw_guest_document (
            id              TEXT PRIMARY KEY,
            tenant_id       TEXT NOT NULL,
            guest_id        TEXT NOT NULL,
            reservation_id  TEXT,
            doc_type        TEXT NOT NULL DEFAULT 'id_photo'
                            CHECK(doc_type IN ('id_photo','passport','registration_signature','other')),
            file_name       TEXT,
            mime_type       TEXT,
            storage_path    TEXT NOT NULL,
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_hclaw_guest_doc_tenant ON hospitalityclaw_guest_document(tenant_id);
        CREATE INDEX IF NOT EXISTS idx_hclaw_guest_doc_res ON hospitalityclaw_guest_document(reservation_id);
        """,
    )

    res_cols = _table_columns(conn, "hospitalityclaw_reservation")
    if not res_cols:
        return

    for col, ddl in (
        ("booking_token", "ALTER TABLE hospitalityclaw_reservation ADD COLUMN booking_token TEXT"),
        ("pre_check_in_at", "ALTER TABLE hospitalityclaw_reservation ADD COLUMN pre_check_in_at TEXT"),
        ("registration_signed_at", "ALTER TABLE hospitalityclaw_reservation ADD COLUMN registration_signed_at TEXT"),
        ("checkout_requested_at", "ALTER TABLE hospitalityclaw_reservation ADD COLUMN checkout_requested_at TEXT"),
    ):
        if col not in res_cols:
            conn.execute(ddl)

    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_hclaw_res_token ON hospitalityclaw_reservation(booking_token)"
    )

    # Rebuild reservation table if CHECK constraint lacks pre_checked_in (SQLite only).
    if _is_postgres(conn):
        return
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'hospitalityclaw_reservation'"
    ).fetchone()
    if not row:
        return
    ddl = row[0] if not hasattr(row, "keys") else row["sql"]
    if not ddl or "pre_checked_in" in ddl:
        return

    conn.execute("DROP TABLE IF EXISTS hospitalityclaw_reservation__mig")
    # Collect current columns for copy
    cols = list(_table_columns(conn, "hospitalityclaw_reservation"))
    col_list = ", ".join(cols)
    _exec_script(
        conn,
        f"""
        CREATE TABLE hospitalityclaw_reservation__mig (
            id              TEXT PRIMARY KEY,
            tenant_id       TEXT NOT NULL,
            hotel_id        TEXT NOT NULL,
            guest_id        TEXT NOT NULL,
            room_id         TEXT,
            room_type_id    TEXT NOT NULL,
            check_in        TEXT NOT NULL,
            check_out       TEXT NOT NULL,
            adults          INTEGER NOT NULL DEFAULT 1,
            children        INTEGER NOT NULL DEFAULT 0,
            status          TEXT NOT NULL DEFAULT 'reserved'
                            CHECK(status IN ('reserved','pre_checked_in','checked_in','checked_out','cancelled','no_show')),
            rate            TEXT NOT NULL DEFAULT '0',
            notes           TEXT,
            booking_token   TEXT,
            pre_check_in_at TEXT,
            registration_signed_at TEXT,
            checkout_requested_at TEXT,
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO hospitalityclaw_reservation__mig ({col_list})
        SELECT {col_list} FROM hospitalityclaw_reservation;
        DROP TABLE hospitalityclaw_reservation;
        ALTER TABLE hospitalityclaw_reservation__mig RENAME TO hospitalityclaw_reservation;
        CREATE INDEX IF NOT EXISTS idx_hclaw_res_tenant ON hospitalityclaw_reservation(tenant_id);
        CREATE INDEX IF NOT EXISTS idx_hclaw_res_dates ON hospitalityclaw_reservation(tenant_id, check_in, check_out);
        CREATE INDEX IF NOT EXISTS idx_hclaw_res_status ON hospitalityclaw_reservation(tenant_id, status);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_hclaw_res_token ON hospitalityclaw_reservation(booking_token);
        """
    )

    # Backfill tokens for existing reservations
    import secrets
    import uuid as _uuid

    missing = conn.execute(
        "SELECT id FROM hospitalityclaw_reservation WHERE booking_token IS NULL OR booking_token = ''"
    ).fetchall()
    for m in missing:
        rid = m[0] if isinstance(m, tuple) else m["id"]
        token = secrets.token_urlsafe(24)
        try:
            conn.execute(
                "UPDATE hospitalityclaw_reservation SET booking_token = ? WHERE id = ?",
                (token, rid),
            )
        except Exception:
            conn.execute(
                "UPDATE hospitalityclaw_reservation SET booking_token = ? WHERE id = ?",
                (secrets.token_urlsafe(24) + _uuid.uuid4().hex[:8], rid),
            )

def _migrate_ledger_source(conn) -> None:
    """Mark guest-ledger rows with the selling channel (Booking.com, Airbnb, …)."""
    try:
        cols = _table_columns(conn, "hospitalityclaw_ledger_entry")
    except Exception:
        return
    if cols and "source" not in cols:
        conn.execute("ALTER TABLE hospitalityclaw_ledger_entry ADD COLUMN source TEXT")


def _migrate_folio_status_cancelled(conn) -> None:
    """Allow folio status = cancelled (SQLite CHECK is baked into CREATE TABLE)."""
    if _is_postgres(conn):
        return
    conn.execute("DROP TABLE IF EXISTS hospitalityclaw_folio__mig")
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'hospitalityclaw_folio'"
    ).fetchone()
    if not row:
        return
    ddl = row[0] if not hasattr(row, "keys") else row["sql"]
    if not ddl or "cancelled" in ddl:
        return
    _exec_script(
        conn,
        """
        CREATE TABLE hospitalityclaw_folio__mig (
            id              TEXT PRIMARY KEY,
            tenant_id       TEXT NOT NULL,
            reservation_id  TEXT NOT NULL,
            guest_id        TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'open'
                            CHECK(status IN ('open','closed','cancelled')),
            balance         TEXT NOT NULL DEFAULT '0',
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(tenant_id, reservation_id)
        );
        INSERT INTO hospitalityclaw_folio__mig
            (id, tenant_id, reservation_id, guest_id, status, balance, created_at, updated_at)
        SELECT id, tenant_id, reservation_id, guest_id, status, balance, created_at, updated_at
        FROM hospitalityclaw_folio;
        DROP TABLE hospitalityclaw_folio;
        ALTER TABLE hospitalityclaw_folio__mig RENAME TO hospitalityclaw_folio;
        CREATE INDEX IF NOT EXISTS idx_hclaw_folio_tenant ON hospitalityclaw_folio(tenant_id);
        """
    )


def _backfill_cancelled_folios(conn) -> None:
    """Mark folios cancelled when their reservation is already cancelled."""
    conn.execute(
        """UPDATE hospitalityclaw_folio
           SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
           WHERE status != 'cancelled'
             AND reservation_id IN (
               SELECT id FROM hospitalityclaw_reservation WHERE status = 'cancelled'
             )"""
    )