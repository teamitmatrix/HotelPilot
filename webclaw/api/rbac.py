"""Webclaw RBAC engine — named permissions, system roles, FastAPI guard.

Tables (canonical names in SQL views: roles, permissions, user_roles):
  webclaw_role              roles
  webclaw_permission        permissions catalog (bookings.write, …)
  webclaw_user_role         user_roles (optional tenant_id on the assignment)
  webclaw_role_grant        role → named permission
  webclaw_role_permission   skill/action globs used by the skill middleware

Resolution for a skill action:
  1. No users yet → allow (bootstrap)
  2. SUPER_ADMIN / System Manager → allow
  3. Named grants covering (skill, action) via fnmatch
  4. Legacy webclaw_role_permission globs
  5. Deny

``@require_permission("bookings.write")`` checks the named catalog against
the caller's roles **and** their ``tenant_id`` (hotel staff cannot exercise
a grant on another hotel).
"""
from __future__ import annotations

import fnmatch
import functools
import inspect
import uuid
from typing import Callable

from fastapi import HTTPException, Request

from db_access import DbConn, list_relations

# ── Role codes (stable API) ─────────────────────────────────────────────────

SUPER_ADMIN = "SUPER_ADMIN"
PLATFORM_SUPPORT = "PLATFORM_SUPPORT"
PLATFORM_BILLING = "PLATFORM_BILLING"
PLATFORM_SECURITY = "PLATFORM_SECURITY"
PARTNER_ADMIN = "PARTNER_ADMIN"
HOTEL_OWNER = "HOTEL_OWNER"
HOTEL_ADMIN = "HOTEL_ADMIN"
GENERAL_MANAGER = "GENERAL_MANAGER"
HOTEL_MANAGER = "HOTEL_MANAGER"
FRONT_DESK = "FRONT_DESK"
HOUSEKEEPING = "HOUSEKEEPING"
HOTEL_ACCOUNTANT = "HOTEL_ACCOUNTANT"
# Branch accountant (legacy code ACCOUNTANT kept for existing assignments).
ACCOUNTANT = "ACCOUNTANT"
BRANCH_ACCOUNTANT = ACCOUNTANT

ROLE_CODES = (
    SUPER_ADMIN,
    PLATFORM_SUPPORT,
    PLATFORM_BILLING,
    PLATFORM_SECURITY,
    PARTNER_ADMIN,
    HOTEL_OWNER,
    HOTEL_ADMIN,
    GENERAL_MANAGER,
    HOTEL_MANAGER,
    FRONT_DESK,
    HOUSEKEEPING,
    HOTEL_ACCOUNTANT,
    ACCOUNTANT,
)

# Display names kept for existing JWT / UI (System Manager = SUPER_ADMIN).
ROLE_DISPLAY = {
    SUPER_ADMIN: "System Manager",
    PLATFORM_SUPPORT: "Platform Support",
    PLATFORM_BILLING: "Platform Billing",
    PLATFORM_SECURITY: "Platform Security",
    PARTNER_ADMIN: "Partner Admin",
    HOTEL_OWNER: "Hotel Owner",
    HOTEL_ADMIN: "Hotel Admin",
    GENERAL_MANAGER: "General Manager",
    HOTEL_MANAGER: "Hotel Manager",
    FRONT_DESK: "Front Desk",
    HOUSEKEEPING: "Housekeeping",
    HOTEL_ACCOUNTANT: "Hotel Accountant",
    ACCOUNTANT: "Branch Accountant",
}

ROLE_DESCRIPTIONS = {
    SUPER_ADMIN: "Platform SuperAdmin — all tenants and skills",
    PLATFORM_SUPPORT: "Platform support — view tenants, tickets, impersonation, audit",
    PLATFORM_BILLING: "Platform billing — plans, overrides, payment issues",
    PLATFORM_SECURITY: "Platform security — policy, maintenance, backups, audit",
    PARTNER_ADMIN: "Partner / reseller — scoped portfolio console for linked hotels",
    HOTEL_OWNER: "Hotel administrator — full property + books for one tenant",
    HOTEL_ADMIN: "Hotel admin — all branches, settings, staff, reports, and full finance",
    GENERAL_MANAGER: "General manager — all-branch ops and staff (manager and below); reports only for finance",
    HOTEL_MANAGER: "Branch manager — property ops, reports, and invite desk / housekeeping / branch accountant",
    FRONT_DESK: "Front desk — bookings, check-in/out, folio settlement & payments",
    HOUSEKEEPING: "Housekeeping — set room status Clean / Dirty / Maintenance",
    HOTEL_ACCOUNTANT: "Hotel accountant — group-wide night audit, GL, tax, and refund overrides",
    ACCOUNTANT: "Branch accountant — property night audit, GL, tax, and refund overrides (not front-desk ops)",
}

# Every hotel staff role that can appear on a membership (excluding Owner bootstrap).
HOTEL_STAFF_ROLES = (
    HOTEL_ADMIN,
    GENERAL_MANAGER,
    HOTEL_MANAGER,
    FRONT_DESK,
    HOUSEKEEPING,
    HOTEL_ACCOUNTANT,
    ACCOUNTANT,
)

# Roles that span every property in the hotel group.
GROUP_WIDE_ROLES = (
    HOTEL_OWNER,
    HOTEL_ADMIN,
    GENERAL_MANAGER,
    HOTEL_ACCOUNTANT,
)

# Who may invite whom (actor role code → assignable role codes).
ASSIGNABLE_ROLES_BY_ACTOR: dict[str, tuple[str, ...]] = {
    HOTEL_OWNER: (
        HOTEL_ADMIN,
        GENERAL_MANAGER,
        HOTEL_MANAGER,
        FRONT_DESK,
        HOUSEKEEPING,
        HOTEL_ACCOUNTANT,
        ACCOUNTANT,
    ),
    HOTEL_ADMIN: (
        HOTEL_ADMIN,
        GENERAL_MANAGER,
        HOTEL_MANAGER,
        FRONT_DESK,
        HOUSEKEEPING,
        HOTEL_ACCOUNTANT,
        ACCOUNTANT,
    ),
    GENERAL_MANAGER: (
        HOTEL_MANAGER,
        FRONT_DESK,
        HOUSEKEEPING,
        ACCOUNTANT,
    ),
    HOTEL_MANAGER: (
        FRONT_DESK,
        HOUSEKEEPING,
        ACCOUNTANT,
    ),
}

# Union used for validation / role replacement.
HOTEL_STAFF_ASSIGNABLE_ROLES = tuple(
    dict.fromkeys(
        code
        for roles in ASSIGNABLE_ROLES_BY_ACTOR.values()
        for code in roles
    )
)


def assignable_roles_for_actor(actor_role_code: str | None) -> tuple[str, ...]:
    """Return role codes the actor may assign; empty if they cannot manage staff."""
    if not actor_role_code:
        return ()
    return ASSIGNABLE_ROLES_BY_ACTOR.get(str(actor_role_code).strip().upper(), ())

# Named permission catalog. Each name maps to skill/action globs.
PERMISSION_CATALOG: dict[str, dict] = {
    "platform.admin": {
        "description": "Full platform administration across all tenants",
        "scope": "platform",
        "actions": [("*", "*")],
    },
    "platform.tenants.view": {
        "description": "View hotel tenants and platform analytics",
        "scope": "platform",
        "actions": [],
    },
    "platform.tenants.manage": {
        "description": "Create, suspend, and edit hotel tenants",
        "scope": "platform",
        "actions": [],
    },
    "platform.billing.manage": {
        "description": "Manage plans, billing overrides, and payment issues",
        "scope": "platform",
        "actions": [],
    },
    "platform.support.manage": {
        "description": "Manage support tickets and impersonate hotels",
        "scope": "platform",
        "actions": [],
    },
    "platform.audit.view": {
        "description": "View and export platform audit logs",
        "scope": "platform",
        "actions": [],
    },
    "platform.security.manage": {
        "description": "Security policy, maintenance mode, and backups",
        "scope": "platform",
        "actions": [],
    },
    "platform.settings.manage": {
        "description": "Platform settings, integrations, and announcements",
        "scope": "platform",
        "actions": [],
    },
    "partner.console.view": {
        "description": "View partner/reseller scoped hotel portfolio",
        "scope": "platform",
        "actions": [],
    },
    "hotel.manage": {
        "description": "Full hotel operations for the bound tenant",
        "scope": "tenant",
        "actions": [
            ("hospitalityclaw", "*"),
            ("erpclaw", "list-*"),
            ("erpclaw", "get-*"),
            ("erpclaw", "add-*"),
            ("erpclaw", "update-*"),
            ("erpclaw", "submit-*"),
            ("erpclaw", "cancel-*"),
        ],
    },
    "hotel.staff.manage": {
        "description": "Invite and manage hotel staff within the actor's assignable roles",
        "scope": "tenant",
        "actions": [],
    },
    "reports.generate": {
        "description": "View and generate rooms, bookings, invoice, and check-in/out reports",
        "scope": "tenant",
        "actions": [
            ("hospitalityclaw", "list-revenue"),
            ("hospitalityclaw", "hospitality-list-revenue"),
            ("hospitalityclaw", "list-reservations"),
            ("hospitalityclaw", "get-reservation"),
            ("hospitalityclaw", "hospitality-list-reservations"),
            ("hospitalityclaw", "hospitality-get-reservation"),
            ("hospitalityclaw", "list-rooms"),
            ("hospitalityclaw", "get-room"),
            ("hospitalityclaw", "hospitality-list-rooms"),
            ("hospitalityclaw", "hospitality-get-room"),
            ("hospitalityclaw", "list-folios"),
            ("hospitalityclaw", "get-folio"),
            ("hospitalityclaw", "hospitality-list-folios"),
            ("hospitalityclaw", "hospitality-get-folio"),
            ("hospitalityclaw", "list-transactions"),
            ("hospitalityclaw", "hospitality-list-transactions"),
            ("erpclaw", "list-sales-invoices"),
            ("erpclaw", "get-sales-invoice"),
            ("erpclaw", "list-customers"),
            ("erpclaw", "get-customer"),
            ("erpclaw", "profit-and-loss"),
            ("erpclaw", "trial-balance"),
            ("erpclaw", "balance-sheet"),
            ("erpclaw", "general-ledger"),
            ("erpclaw", "tax-summary"),
            ("erpclaw", "tax-status"),
        ],
    },
    "bookings.read": {
        "description": "View reservations",
        "scope": "tenant",
        "actions": [
            ("hospitalityclaw", "list-reservations"),
            ("hospitalityclaw", "get-reservation"),
            ("hospitalityclaw", "hospitality-list-reservations"),
            ("hospitalityclaw", "hospitality-get-reservation"),
        ],
    },
    "bookings.write": {
        "description": "Create and update reservations",
        "scope": "tenant",
        "actions": [
            ("hospitalityclaw", "add-reservation"),
            ("hospitalityclaw", "hospitality-add-reservation"),
            ("hospitalityclaw", "assign-room"),
            ("hospitalityclaw", "hospitality-assign-room"),
            ("hospitalityclaw", "cancel-reservation"),
            ("hospitalityclaw", "hospitality-cancel-reservation"),
        ],
    },
    "guests.read": {
        "description": "View guests",
        "scope": "tenant",
        "actions": [
            ("hospitalityclaw", "list-guests"),
            ("hospitalityclaw", "get-guest"),
            ("hospitalityclaw", "hospitality-list-guests"),
            ("hospitalityclaw", "hospitality-get-guest"),
        ],
    },
    "guests.write": {
        "description": "Create and update guests",
        "scope": "tenant",
        "actions": [
            ("hospitalityclaw", "add-guest"),
            ("hospitalityclaw", "hospitality-add-guest"),
        ],
    },
    "reservations.check_in": {
        "description": "Check guests in",
        "scope": "tenant",
        "actions": [
            ("hospitalityclaw", "check-in"),
            ("hospitalityclaw", "hospitality-check-in"),
            ("hospitalityclaw", "check-*"),
        ],
    },
    "reservations.check_out": {
        "description": "Check guests out",
        "scope": "tenant",
        "actions": [
            ("hospitalityclaw", "check-out"),
            ("hospitalityclaw", "hospitality-check-out"),
        ],
    },
    "rooms.read": {
        "description": "View rooms and status",
        "scope": "tenant",
        "actions": [
            ("hospitalityclaw", "list-rooms"),
            ("hospitalityclaw", "get-room"),
            ("hospitalityclaw", "list-room-types"),
            ("hospitalityclaw", "get-room-type"),
            ("hospitalityclaw", "list-hotels"),
            ("hospitalityclaw", "get-hotel"),
            ("hospitalityclaw", "status"),
            ("hospitalityclaw", "rate-calendar"),
            ("hospitalityclaw", "hospitality-list-rooms"),
            ("hospitalityclaw", "hospitality-get-room"),
            ("hospitalityclaw", "hospitality-list-room-types"),
            ("hospitalityclaw", "hospitality-get-room-type"),
            ("hospitalityclaw", "hospitality-list-hotels"),
            ("hospitalityclaw", "hospitality-get-hotel"),
            ("hospitalityclaw", "hospitality-status"),
            ("hospitalityclaw", "hospitality-rate-calendar"),
        ],
    },
    "rooms.write": {
        "description": "Create and update room types and room numbers",
        "scope": "tenant",
        "actions": [
            ("hospitalityclaw", "add-room-type"),
            ("hospitalityclaw", "update-room-type"),
            ("hospitalityclaw", "delete-room-type"),
            ("hospitalityclaw", "add-room"),
            ("hospitalityclaw", "update-room"),
            ("hospitalityclaw", "delete-room"),
            ("hospitalityclaw", "hospitality-add-room-type"),
            ("hospitalityclaw", "hospitality-update-room-type"),
            ("hospitalityclaw", "hospitality-delete-room-type"),
            ("hospitalityclaw", "hospitality-add-room"),
            ("hospitalityclaw", "hospitality-update-room"),
            ("hospitalityclaw", "hospitality-delete-room"),
            ("hospitalityclaw", "add-rate-plan"),
            ("hospitalityclaw", "update-rate-plan"),
            ("hospitalityclaw", "delete-rate-plan"),
            ("hospitalityclaw", "list-rate-plans"),
            ("hospitalityclaw", "set-rate-window"),
            ("hospitalityclaw", "rate-calendar"),
            ("hospitalityclaw", "hospitality-add-rate-plan"),
            ("hospitalityclaw", "hospitality-update-rate-plan"),
            ("hospitalityclaw", "hospitality-delete-rate-plan"),
            ("hospitalityclaw", "hospitality-list-rate-plans"),
            ("hospitalityclaw", "hospitality-set-rate-window"),
            ("hospitalityclaw", "hospitality-rate-calendar"),
            ("hospitalityclaw", "add-tax-rule"),
            ("hospitalityclaw", "update-tax-rule"),
            ("hospitalityclaw", "delete-tax-rule"),
            ("hospitalityclaw", "list-tax-rules"),
            ("hospitalityclaw", "hospitality-add-tax-rule"),
            ("hospitalityclaw", "hospitality-update-tax-rule"),
            ("hospitalityclaw", "hospitality-delete-tax-rule"),
            ("hospitalityclaw", "hospitality-list-tax-rules"),
        ],
    },
    "rooms.update_status": {
        "description": "Set room status Clean / Dirty / Maintenance",
        "scope": "tenant",
        "actions": [
            ("hospitalityclaw", "update-room-status"),
            ("hospitalityclaw", "hospitality-update-room-status"),
        ],
    },
    "folios.read": {
        "description": "View guest folios and charges",
        "scope": "tenant",
        "actions": [
            ("hospitalityclaw", "list-folios"),
            ("hospitalityclaw", "get-folio"),
            ("hospitalityclaw", "list-folio-charges"),
            ("hospitalityclaw", "list-revenue"),
            ("hospitalityclaw", "status"),
            ("hospitalityclaw", "hospitality-list-folios"),
            ("hospitalityclaw", "hospitality-get-folio"),
            ("hospitalityclaw", "hospitality-list-folio-charges"),
            ("hospitalityclaw", "hospitality-list-revenue"),
            ("hospitalityclaw", "hospitality-status"),
        ],
    },
    "folios.write": {
        "description": "Routine folio charges, payments, and settlements (front desk)",
        "scope": "tenant",
        "actions": [
            ("hospitalityclaw", "add-folio-charge"),
            ("hospitalityclaw", "settle-folio"),
            ("hospitalityclaw", "post-room-tariff"),
            ("hospitalityclaw", "hospitality-add-folio-charge"),
            ("hospitalityclaw", "hospitality-settle-folio"),
            ("hospitalityclaw", "hospitality-post-room-tariff"),
        ],
    },
    "ledger.read": {
        "description": "View general ledger and folio ledger entries",
        "scope": "tenant",
        "actions": [
            ("hospitalityclaw", "list-ledger-entries"),
            ("hospitalityclaw", "list-transactions"),
            ("hospitalityclaw", "hospitality-list-ledger-entries"),
            ("hospitalityclaw", "hospitality-list-transactions"),
            ("erpclaw", "list-gl-entries"),
            ("erpclaw", "list-accounts"),
            ("erpclaw", "get-account"),
            ("erpclaw", "list-journal-entries"),
            ("erpclaw", "get-journal-entry"),
            ("erpclaw", "trial-balance"),
            ("erpclaw", "general-ledger"),
            ("erpclaw", "profit-and-loss"),
            ("erpclaw", "balance-sheet"),
            ("erpclaw", "list-payments"),
            ("erpclaw", "get-payment"),
        ],
    },
    "ledger.write": {
        "description": "Manual journal entries and GL adjustments",
        "scope": "tenant",
        "actions": [
            ("erpclaw", "add-journal-entry"),
            ("erpclaw", "update-journal-entry"),
            ("erpclaw", "submit-journal-entry"),
            ("erpclaw", "cancel-journal-entry"),
            ("erpclaw", "delete-journal-entry"),
            ("erpclaw", "post-gl-entries"),
            ("erpclaw", "reverse-gl-entries"),
            ("erpclaw", "add-payment"),
            ("erpclaw", "create-payment"),
        ],
    },
    "invoices.read": {
        "description": "View sales and purchase invoices (city / corporate ledger)",
        "scope": "tenant",
        "actions": [
            ("erpclaw", "list-sales-invoices"),
            ("erpclaw", "get-sales-invoice"),
            ("erpclaw", "list-purchase-invoices"),
            ("erpclaw", "get-purchase-invoice"),
            ("erpclaw", "list-customers"),
            ("erpclaw", "get-customer"),
        ],
    },
    "invoices.write": {
        "description": "Create and submit AR / corporate city-ledger invoices",
        "scope": "tenant",
        "actions": [
            ("erpclaw", "create-sales-invoice"),
            ("erpclaw", "add-sales-invoice"),
            ("erpclaw", "update-sales-invoice"),
            ("erpclaw", "submit-sales-invoice"),
            ("erpclaw", "cancel-sales-invoice"),
            ("erpclaw", "create-purchase-invoice"),
            ("erpclaw", "submit-purchase-invoice"),
            ("erpclaw", "add-customer"),
            ("erpclaw", "update-customer"),
        ],
    },
    "finance.night_audit": {
        "description": "Night audit / end-of-day closeout and period validation",
        "scope": "tenant",
        "actions": [
            ("hospitalityclaw", "list-revenue"),
            ("hospitalityclaw", "list-transactions"),
            ("hospitalityclaw", "list-ledger-entries"),
            ("hospitalityclaw", "status"),
            ("hospitalityclaw", "hospitality-list-revenue"),
            ("hospitalityclaw", "hospitality-list-transactions"),
            ("hospitalityclaw", "hospitality-list-ledger-entries"),
            ("hospitalityclaw", "hospitality-status"),
            ("hospitalityclaw", "post-room-tariff"),
            ("hospitalityclaw", "hospitality-post-room-tariff"),
            ("erpclaw", "validate-period-close"),
            ("erpclaw", "close-fiscal-year"),
            ("erpclaw", "reopen-fiscal-year"),
            ("erpclaw", "list-fiscal-years"),
            ("erpclaw", "trial-balance"),
            ("erpclaw", "general-ledger"),
        ],
    },
    "finance.tax_reports": {
        "description": "Tax liability summaries and financial audit reports",
        "scope": "tenant",
        "actions": [
            ("erpclaw", "tax-summary"),
            ("erpclaw", "tax-status"),
            ("erpclaw", "list-tax-templates"),
            ("erpclaw", "get-tax-template"),
            ("erpclaw", "list-tax-rules"),
            ("erpclaw", "list-tax-categories"),
            ("erpclaw", "calculate-tax"),
            ("erpclaw", "generate-1099-data"),
            ("erpclaw", "profit-and-loss"),
            ("erpclaw", "balance-sheet"),
            ("erpclaw", "trial-balance"),
            ("hospitalityclaw", "list-tax-rules"),
            ("hospitalityclaw", "hospitality-list-tax-rules"),
        ],
    },
    "finance.refunds": {
        "description": "Refund and discount override approvals (not routine checkout payments)",
        "scope": "tenant",
        "actions": [
            ("hospitalityclaw", "add-folio-charge"),
            ("hospitalityclaw", "settle-folio"),
            ("hospitalityclaw", "hospitality-add-folio-charge"),
            ("hospitalityclaw", "hospitality-settle-folio"),
            ("erpclaw", "cancel-sales-invoice"),
            ("erpclaw", "add-journal-entry"),
            ("erpclaw", "submit-journal-entry"),
            ("erpclaw", "add-payment"),
        ],
    },
}

# Skill/action globs copied onto webclaw_role_permission for the middleware.
ROLE_SKILL_PATTERNS: dict[str, list[tuple[str, str]]] = {
    SUPER_ADMIN: [("*", "*")],
    HOTEL_OWNER: [
        ("hospitalityclaw", "*"),
        ("erpclaw", "list-*"),
        ("erpclaw", "get-*"),
        ("erpclaw", "add-*"),
        ("erpclaw", "update-*"),
        ("erpclaw", "submit-*"),
        ("erpclaw", "cancel-*"),
    ],
    HOTEL_ADMIN: [
        ("hospitalityclaw", "*"),
        ("erpclaw", "list-*"),
        ("erpclaw", "get-*"),
        ("erpclaw", "add-*"),
        ("erpclaw", "update-*"),
        ("erpclaw", "submit-*"),
        ("erpclaw", "cancel-*"),
    ],
    GENERAL_MANAGER: [
        ("hospitalityclaw", "list-*"),
        ("hospitalityclaw", "get-*"),
        ("hospitalityclaw", "add-*"),
        ("hospitalityclaw", "update-*"),
        ("hospitalityclaw", "check-*"),
        ("hospitalityclaw", "hospitality-*"),
        ("hospitalityclaw", "update-room-status"),
        ("erpclaw", "list-*"),
        ("erpclaw", "get-*"),
        ("erpclaw", "profit-and-loss"),
        ("erpclaw", "trial-balance"),
        ("erpclaw", "balance-sheet"),
        ("erpclaw", "general-ledger"),
        ("erpclaw", "tax-summary"),
        ("erpclaw", "tax-status"),
    ],
    HOTEL_MANAGER: [
        ("hospitalityclaw", "list-*"),
        ("hospitalityclaw", "get-*"),
        ("hospitalityclaw", "add-*"),
        ("hospitalityclaw", "update-*"),
        ("hospitalityclaw", "check-*"),
        ("hospitalityclaw", "hospitality-*"),
        ("hospitalityclaw", "update-room-status"),
        ("erpclaw", "list-*"),
        ("erpclaw", "get-*"),
        ("erpclaw", "profit-and-loss"),
        ("erpclaw", "trial-balance"),
        ("erpclaw", "balance-sheet"),
        ("erpclaw", "tax-summary"),
        ("erpclaw", "tax-status"),
    ],
    FRONT_DESK: [
        ("hospitalityclaw", "list-reservations"),
        ("hospitalityclaw", "get-reservation"),
        ("hospitalityclaw", "add-reservation"),
        ("hospitalityclaw", "list-guests"),
        ("hospitalityclaw", "get-guest"),
        ("hospitalityclaw", "add-guest"),
        ("hospitalityclaw", "check-in"),
        ("hospitalityclaw", "check-out"),
        ("hospitalityclaw", "check-*"),
        ("hospitalityclaw", "list-rooms"),
        ("hospitalityclaw", "get-room"),
        ("hospitalityclaw", "list-room-types"),
        ("hospitalityclaw", "get-room-type"),
        ("hospitalityclaw", "list-hotels"),
        ("hospitalityclaw", "get-hotel"),
        ("hospitalityclaw", "status"),
        ("hospitalityclaw", "list-folios"),
        ("hospitalityclaw", "get-folio"),
        ("hospitalityclaw", "list-folio-charges"),
        ("hospitalityclaw", "list-revenue"),
        ("hospitalityclaw", "add-folio-charge"),
        ("hospitalityclaw", "settle-folio"),
        ("hospitalityclaw", "post-room-tariff"),
        ("hospitalityclaw", "hospitality-list-reservations"),
        ("hospitalityclaw", "hospitality-get-reservation"),
        ("hospitalityclaw", "hospitality-add-reservation"),
        ("hospitalityclaw", "hospitality-list-guests"),
        ("hospitalityclaw", "hospitality-get-guest"),
        ("hospitalityclaw", "hospitality-add-guest"),
        ("hospitalityclaw", "hospitality-check-in"),
        ("hospitalityclaw", "hospitality-check-out"),
        ("hospitalityclaw", "hospitality-list-rooms"),
        ("hospitalityclaw", "hospitality-get-room"),
        ("hospitalityclaw", "hospitality-list-room-types"),
        ("hospitalityclaw", "hospitality-get-room-type"),
        ("hospitalityclaw", "hospitality-list-hotels"),
        ("hospitalityclaw", "hospitality-get-hotel"),
        ("hospitalityclaw", "hospitality-status"),
        ("hospitalityclaw", "hospitality-list-folios"),
        ("hospitalityclaw", "hospitality-get-folio"),
        ("hospitalityclaw", "hospitality-list-folio-charges"),
        ("hospitalityclaw", "hospitality-list-revenue"),
        ("hospitalityclaw", "hospitality-add-folio-charge"),
        ("hospitalityclaw", "hospitality-settle-folio"),
        ("hospitalityclaw", "hospitality-post-room-tariff"),
    ],
    HOUSEKEEPING: [
        ("hospitalityclaw", "list-rooms"),
        ("hospitalityclaw", "get-room"),
        ("hospitalityclaw", "hospitality-list-rooms"),
        ("hospitalityclaw", "hospitality-get-room"),
        ("hospitalityclaw", "update-room-status"),
        ("hospitalityclaw", "hospitality-update-room-status"),
    ],
    HOTEL_ACCOUNTANT: [
        ("hospitalityclaw", "list-folios"),
        ("hospitalityclaw", "get-folio"),
        ("hospitalityclaw", "list-folio-charges"),
        ("hospitalityclaw", "list-revenue"),
        ("hospitalityclaw", "list-ledger-entries"),
        ("hospitalityclaw", "list-transactions"),
        ("hospitalityclaw", "status"),
        ("hospitalityclaw", "list-tax-rules"),
        ("hospitalityclaw", "hospitality-list-folios"),
        ("hospitalityclaw", "hospitality-get-folio"),
        ("hospitalityclaw", "hospitality-list-folio-charges"),
        ("hospitalityclaw", "hospitality-list-revenue"),
        ("hospitalityclaw", "hospitality-list-ledger-entries"),
        ("hospitalityclaw", "hospitality-list-transactions"),
        ("hospitalityclaw", "hospitality-status"),
        ("hospitalityclaw", "hospitality-list-tax-rules"),
        ("hospitalityclaw", "post-room-tariff"),
        ("hospitalityclaw", "hospitality-post-room-tariff"),
        ("hospitalityclaw", "add-folio-charge"),
        ("hospitalityclaw", "settle-folio"),
        ("hospitalityclaw", "hospitality-add-folio-charge"),
        ("hospitalityclaw", "hospitality-settle-folio"),
        ("erpclaw", "list-*"),
        ("erpclaw", "get-*"),
        ("erpclaw", "add-journal-entry"),
        ("erpclaw", "update-journal-entry"),
        ("erpclaw", "submit-journal-entry"),
        ("erpclaw", "cancel-journal-entry"),
        ("erpclaw", "delete-journal-entry"),
        ("erpclaw", "post-gl-entries"),
        ("erpclaw", "reverse-gl-entries"),
        ("erpclaw", "add-payment"),
        ("erpclaw", "create-payment"),
        ("erpclaw", "create-sales-invoice"),
        ("erpclaw", "add-sales-invoice"),
        ("erpclaw", "update-sales-invoice"),
        ("erpclaw", "submit-sales-invoice"),
        ("erpclaw", "cancel-sales-invoice"),
        ("erpclaw", "create-purchase-invoice"),
        ("erpclaw", "submit-purchase-invoice"),
        ("erpclaw", "add-customer"),
        ("erpclaw", "update-customer"),
        ("erpclaw", "trial-balance"),
        ("erpclaw", "general-ledger"),
        ("erpclaw", "profit-and-loss"),
        ("erpclaw", "balance-sheet"),
        ("erpclaw", "tax-summary"),
        ("erpclaw", "tax-status"),
        ("erpclaw", "list-tax-templates"),
        ("erpclaw", "calculate-tax"),
        ("erpclaw", "generate-1099-data"),
        ("erpclaw", "validate-period-close"),
        ("erpclaw", "close-fiscal-year"),
        ("erpclaw", "reopen-fiscal-year"),
        ("erpclaw", "list-fiscal-years"),
    ],
    ACCOUNTANT: [
        # Folio inspection only — routine settle stays with Front Desk
        ("hospitalityclaw", "list-folios"),
        ("hospitalityclaw", "get-folio"),
        ("hospitalityclaw", "list-folio-charges"),
        ("hospitalityclaw", "list-revenue"),
        ("hospitalityclaw", "list-ledger-entries"),
        ("hospitalityclaw", "list-transactions"),
        ("hospitalityclaw", "status"),
        ("hospitalityclaw", "list-tax-rules"),
        ("hospitalityclaw", "hospitality-list-folios"),
        ("hospitalityclaw", "hospitality-get-folio"),
        ("hospitalityclaw", "hospitality-list-folio-charges"),
        ("hospitalityclaw", "hospitality-list-revenue"),
        ("hospitalityclaw", "hospitality-list-ledger-entries"),
        ("hospitalityclaw", "hospitality-list-transactions"),
        ("hospitalityclaw", "hospitality-status"),
        ("hospitalityclaw", "hospitality-list-tax-rules"),
        ("hospitalityclaw", "post-room-tariff"),
        ("hospitalityclaw", "hospitality-post-room-tariff"),
        # Refund / discount overrides (not check-in/out)
        ("hospitalityclaw", "add-folio-charge"),
        ("hospitalityclaw", "settle-folio"),
        ("hospitalityclaw", "hospitality-add-folio-charge"),
        ("hospitalityclaw", "hospitality-settle-folio"),
        # ERP — GL, journals, AR, tax, night audit / period close
        ("erpclaw", "list-*"),
        ("erpclaw", "get-*"),
        ("erpclaw", "add-journal-entry"),
        ("erpclaw", "update-journal-entry"),
        ("erpclaw", "submit-journal-entry"),
        ("erpclaw", "cancel-journal-entry"),
        ("erpclaw", "delete-journal-entry"),
        ("erpclaw", "post-gl-entries"),
        ("erpclaw", "reverse-gl-entries"),
        ("erpclaw", "add-payment"),
        ("erpclaw", "create-payment"),
        ("erpclaw", "create-sales-invoice"),
        ("erpclaw", "add-sales-invoice"),
        ("erpclaw", "update-sales-invoice"),
        ("erpclaw", "submit-sales-invoice"),
        ("erpclaw", "cancel-sales-invoice"),
        ("erpclaw", "create-purchase-invoice"),
        ("erpclaw", "submit-purchase-invoice"),
        ("erpclaw", "add-customer"),
        ("erpclaw", "update-customer"),
        ("erpclaw", "trial-balance"),
        ("erpclaw", "general-ledger"),
        ("erpclaw", "profit-and-loss"),
        ("erpclaw", "balance-sheet"),
        ("erpclaw", "tax-summary"),
        ("erpclaw", "tax-status"),
        ("erpclaw", "list-tax-templates"),
        ("erpclaw", "calculate-tax"),
        ("erpclaw", "generate-1099-data"),
        ("erpclaw", "validate-period-close"),
        ("erpclaw", "close-fiscal-year"),
        ("erpclaw", "reopen-fiscal-year"),
        ("erpclaw", "list-fiscal-years"),
    ],
}
ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    SUPER_ADMIN: ("platform.admin",),
    PLATFORM_SUPPORT: (
        "platform.tenants.view",
        "platform.support.manage",
        "platform.audit.view",
    ),
    PLATFORM_BILLING: (
        "platform.tenants.view",
        "platform.billing.manage",
        "platform.audit.view",
    ),
    PLATFORM_SECURITY: (
        "platform.security.manage",
        "platform.audit.view",
        "platform.settings.manage",
    ),
    PARTNER_ADMIN: (
        "partner.console.view",
    ),
    HOTEL_OWNER: (
        "hotel.manage",
        "hotel.staff.manage",
        "reports.generate",
        "bookings.read", "bookings.write",
        "guests.read", "guests.write",
        "reservations.check_in", "reservations.check_out",
        "rooms.read", "rooms.write", "rooms.update_status",
        "folios.read", "folios.write",
        "ledger.read", "ledger.write",
        "invoices.read", "invoices.write",
        "finance.night_audit", "finance.tax_reports", "finance.refunds",
    ),
    HOTEL_ADMIN: (
        "hotel.manage",
        "hotel.staff.manage",
        "reports.generate",
        "bookings.read", "bookings.write",
        "guests.read", "guests.write",
        "reservations.check_in", "reservations.check_out",
        "rooms.read", "rooms.write", "rooms.update_status",
        "folios.read", "folios.write",
        "ledger.read", "ledger.write",
        "invoices.read", "invoices.write",
        "finance.night_audit", "finance.tax_reports", "finance.refunds",
    ),
    GENERAL_MANAGER: (
        "hotel.staff.manage",
        "reports.generate",
        "bookings.read", "bookings.write",
        "guests.read", "guests.write",
        "reservations.check_in", "reservations.check_out",
        "rooms.read", "rooms.write", "rooms.update_status",
        "folios.read", "folios.write",
        "ledger.read", "invoices.read",
    ),
    HOTEL_MANAGER: (
        "hotel.staff.manage",
        "reports.generate",
        "bookings.read", "bookings.write",
        "guests.read", "guests.write",
        "reservations.check_in", "reservations.check_out",
        "rooms.read", "rooms.write", "rooms.update_status",
        "folios.read", "folios.write",
        "ledger.read", "invoices.read",
    ),
    FRONT_DESK: (
        "bookings.read", "bookings.write",
        "guests.read", "guests.write",
        "reservations.check_in", "reservations.check_out",
        "rooms.read",
        "folios.read", "folios.write",
    ),
    HOUSEKEEPING: (
        "rooms.read",
        "rooms.update_status",
    ),
    HOTEL_ACCOUNTANT: (
        "folios.read",
        "ledger.read", "ledger.write",
        "invoices.read", "invoices.write",
        "finance.night_audit",
        "finance.tax_reports",
        "finance.refunds",
        "reports.generate",
    ),
    ACCOUNTANT: (
        "folios.read",
        "ledger.read", "ledger.write",
        "invoices.read", "invoices.write",
        "finance.night_audit",
        "finance.tax_reports",
        "finance.refunds",
    ),
}


def _table_cols(conn: DbConn, table: str) -> set[str]:
    from db_access import table_columns

    return table_columns(conn, table)


def _is_superadmin(conn: DbConn, user_id: str) -> bool:
    sql = """SELECT 1 FROM webclaw_user_role ur
             JOIN webclaw_role r ON r.id = ur.role_id
             WHERE ur.user_id = ? AND (r.name = 'System Manager'"""
    params: list = [user_id]
    if "code" in _table_cols(conn, "webclaw_role"):
        sql += " OR r.code = 'SUPER_ADMIN'"
    sql += ") LIMIT 1"
    row = conn.execute(sql, params).fetchone()
    return row is not None


def check_permission(conn: DbConn, user_id: str, skill: str, action: str) -> bool:
    """Check if a user has permission to perform a skill action.

    Returns True if allowed, False if denied.
    Returns True if no users exist (RBAC not yet active).
    """
    row = conn.execute("SELECT COUNT(*) as cnt FROM webclaw_user").fetchone()
    if (row["cnt"] if isinstance(row, dict) else row[0]) == 0:
        return True

    if not user_id:
        return True

    if _is_superadmin(conn, user_id):
        return True

    if _named_grant_covers(conn, user_id, skill, action):
        return True

    role_rows = conn.execute(
        "SELECT DISTINCT role_id FROM webclaw_user_role WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    if not role_rows:
        return False

    role_ids = [r["role_id"] if isinstance(r, dict) else r[0] for r in role_rows]
    placeholders = ",".join("?" for _ in role_ids)
    try:
        perms = conn.execute(
            f"""SELECT skill, action_pattern, allowed
                FROM webclaw_role_permission
                WHERE role_id IN ({placeholders})""",
            role_ids,
        ).fetchall()
    except Exception:
        return False

    for perm in perms:
        p_skill = perm["skill"] if isinstance(perm, dict) else perm[0]
        p_pattern = perm["action_pattern"] if isinstance(perm, dict) else perm[1]
        p_allowed = perm["allowed"] if isinstance(perm, dict) else perm[2]
        if p_skill != skill and p_skill != "*":
            continue
        if fnmatch.fnmatch(action, p_pattern):
            return bool(p_allowed)

    return False


def _named_grant_covers(conn: DbConn, user_id: str, skill: str, action: str) -> bool:
    if "webclaw_role_grant" not in _existing_tables(conn):
        return False
    try:
        rows = conn.execute(
            """SELECT pa.skill, pa.action_pattern
               FROM webclaw_user_role ur
               JOIN webclaw_role_grant g ON g.role_id = ur.role_id
               JOIN webclaw_permission_action pa ON pa.permission_id = g.permission_id
               WHERE ur.user_id = ?""",
            (user_id,),
        ).fetchall()
    except Exception:
        return False
    for row in rows:
        if hasattr(row, "keys"):
            p_skill = row["skill"]
            p_pattern = row["action_pattern"]
        else:
            p_skill = row[0]
            p_pattern = row[1]
        if p_skill != skill and p_skill != "*":
            continue
        if fnmatch.fnmatch(action, p_pattern):
            return True
    return False


def _existing_tables(conn: DbConn) -> set[str]:
    return list_relations(conn)


def _user_has_named_grant(conn: DbConn, user_id: str, permission_name: str) -> bool:
    """True when any of the user's roles grants ``permission_name`` (any tenant scope)."""
    if "webclaw_role_grant" not in _existing_tables(conn):
        return False
    try:
        row = conn.execute(
            """SELECT 1
               FROM webclaw_user_role ur
               JOIN webclaw_role_grant g ON g.role_id = ur.role_id
               JOIN webclaw_permission p ON p.id = g.permission_id
               WHERE ur.user_id = ? AND p.name = ?
               LIMIT 1""",
            (user_id, permission_name),
        ).fetchone()
    except Exception:
        return False
    return row is not None


def has_permission(
    conn: DbConn,
    user_id: str,
    permission_name: str,
    *,
    tenant_id: str | None = None,
    is_superadmin: bool = False,
    impersonating: bool = False,
) -> bool:
    """True if the user holds ``permission_name`` for ``tenant_id``.

    Platform permissions: SuperAdmin, ``platform.admin`` grant, or the specific
    platform.* grant. Tenant permissions require a grant on one of the user's
    roles for their ``tenant_id``. Impersonation suspends platform privileges.
    """
    if not user_id:
        return False
    if impersonating:
        # SuperAdmin acting as a hotel — all platform.* grants are off; tenant
        # grants still apply against the impersonated hotel.
        is_superadmin = False
    elif is_superadmin or _is_superadmin(conn, user_id):
        return True

    meta = PERMISSION_CATALOG.get(permission_name)
    scope = (meta or {}).get("scope", "tenant")
    if scope == "platform":
        if impersonating:
            return False
        if _user_has_named_grant(conn, user_id, "platform.admin"):
            return True
        if permission_name != "platform.admin" and _user_has_named_grant(
            conn, user_id, permission_name
        ):
            return True
        return False

    if tenant_id is None:
        return False

    if "webclaw_role_grant" not in _existing_tables(conn):
        return False

    access_sql = "u.tenant_id IS NULL OR u.tenant_id = ?"
    params: list = [user_id, permission_name, tenant_id]
    if "webclaw_user_tenant" in _existing_tables(conn):
        # A membership lets the same role apply at another property when the
        # role assignment itself is not pinned to a different hotel.
        access_sql += (
            " OR EXISTS (SELECT 1 FROM webclaw_user_tenant ut"
            " WHERE ut.user_id = u.id AND ut.tenant_id = ?)"
        )
        params.append(tenant_id)
    params.append(tenant_id)
    try:
        row = conn.execute(
            f"""SELECT 1
               FROM webclaw_user_role ur
               JOIN webclaw_role_grant g ON g.role_id = ur.role_id
               JOIN webclaw_permission p ON p.id = g.permission_id
               JOIN webclaw_user u ON u.id = ur.user_id
               WHERE ur.user_id = ?
                 AND p.name = ?
                 AND ({access_sql})
                 AND (ur.tenant_id IS NULL OR ur.tenant_id = ?)
               LIMIT 1""",
            params,
        ).fetchone()
    except Exception:
        row = None
    if row is not None:
        return True
    return _membership_grant_allows(conn, user_id, permission_name, tenant_id)


def _membership_grant_allows(
    conn: DbConn,
    user_id: str,
    permission_name: str,
    tenant_id: str,
) -> bool:
    """True when a property membership names a role that holds this permission."""
    if "webclaw_user_tenant" not in _existing_tables(conn):
        return False
    if "webclaw_role_grant" not in _existing_tables(conn):
        return False
    role_match = "mr.name = ut.role_name"
    if "code" in _table_cols(conn, "webclaw_role"):
        role_match = "(mr.code = ut.role_code OR mr.name = ut.role_name)"
    try:
        row = conn.execute(
            f"""SELECT 1
                FROM webclaw_user_tenant ut
                JOIN webclaw_role mr ON {role_match}
                JOIN webclaw_role_grant mg ON mg.role_id = mr.id
                JOIN webclaw_permission mp ON mp.id = mg.permission_id
                WHERE ut.user_id = ? AND ut.tenant_id = ? AND mp.name = ?
                LIMIT 1""",
            (user_id, tenant_id, permission_name),
        ).fetchone()
    except Exception:
        return False
    return row is not None


def _membership_permission_names(
    conn: DbConn,
    user_id: str,
    tenant_id: str,
) -> list[str]:
    if "webclaw_user_tenant" not in _existing_tables(conn):
        return []
    if "webclaw_role_grant" not in _existing_tables(conn):
        return []
    role_match = "mr.name = ut.role_name"
    if "code" in _table_cols(conn, "webclaw_role"):
        role_match = "(mr.code = ut.role_code OR mr.name = ut.role_name)"
    try:
        rows = conn.execute(
            f"""SELECT DISTINCT mp.name
                FROM webclaw_user_tenant ut
                JOIN webclaw_role mr ON {role_match}
                JOIN webclaw_role_grant mg ON mg.role_id = mr.id
                JOIN webclaw_permission mp ON mp.id = mg.permission_id
                WHERE ut.user_id = ? AND ut.tenant_id = ?
                ORDER BY mp.name""",
            (user_id, tenant_id),
        ).fetchall()
    except Exception:
        return []
    return [r["name"] if hasattr(r, "keys") else r[0] for r in rows]


def named_permissions_for_user(
    conn: DbConn,
    user_id: str,
    *,
    is_superadmin: bool = False,
    impersonating: bool = False,
    tenant_id: str | None = None,
) -> list[str]:
    """Named permission catalog entries held by this user (for the UI)."""
    if not user_id:
        return []
    if impersonating:
        return list(ROLE_PERMISSIONS[HOTEL_OWNER])
    if is_superadmin or _is_superadmin(conn, user_id):
        return sorted(PERMISSION_CATALOG.keys())
    if "webclaw_role_grant" not in _existing_tables(conn):
        return []
    tenant_filter = ""
    params: list = [user_id]
    if tenant_id and "tenant_id" in _table_cols(conn, "webclaw_user_role"):
        tenant_filter = "AND (ur.tenant_id IS NULL OR ur.tenant_id = ?)"
        params.append(tenant_id)
    try:
        rows = conn.execute(
            f"""SELECT DISTINCT p.name
               FROM webclaw_user_role ur
               JOIN webclaw_role_grant g ON g.role_id = ur.role_id
               JOIN webclaw_permission p ON p.id = g.permission_id
               WHERE ur.user_id = ?
                 {tenant_filter}
               ORDER BY p.name""",
            params,
        ).fetchall()
    except Exception:
        return []
    names = [r["name"] if hasattr(r, "keys") else r[0] for r in rows]
    if tenant_id:
        for extra in _membership_permission_names(conn, user_id, tenant_id):
            if extra not in names:
                names.append(extra)
        names.sort()
    return names


def _extract_request(args, kwargs) -> Request:
    request = kwargs.get("request")
    if isinstance(request, Request):
        return request
    for value in args:
        if isinstance(value, Request):
            return value
    raise HTTPException(
        status_code=500,
        detail="require_permission needs a FastAPI Request parameter",
    )


def require_permission(permission_name: str):
    """FastAPI route decorator: ``@require_permission("bookings.write")``.

    Checks the named permission against the caller's roles and ``tenant_id``.
    The wrapped endpoint must accept ``request: Request``.
    """

    def decorator(func: Callable):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            _enforce_any_permission(_extract_request(args, kwargs), (permission_name,))
            return await func(*args, **kwargs)

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            _enforce_any_permission(_extract_request(args, kwargs), (permission_name,))
            return func(*args, **kwargs)

        if inspect.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def require_any_permission(*permission_names: str):
    """Allow when the caller holds any listed permission (or platform.admin / SuperAdmin)."""

    def decorator(func: Callable):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            _enforce_any_permission(_extract_request(args, kwargs), permission_names)
            return await func(*args, **kwargs)

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            _enforce_any_permission(_extract_request(args, kwargs), permission_names)
            return func(*args, **kwargs)

        if inspect.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def _enforce_any_permission(
    request: Request, permission_names: tuple[str, ...] | list[str]
) -> None:
    from auth.deps import get_optional_tenant
    import db

    ctx = get_optional_tenant(request)
    if ctx is None or not ctx.user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    conn = db.get_connection()
    try:
        for permission_name in permission_names:
            allowed = has_permission(
                conn,
                ctx.user_id,
                permission_name,
                tenant_id=ctx.tenant_id,
                is_superadmin=ctx.is_superadmin,
                impersonating=getattr(ctx, "impersonating", False),
            )
            if allowed:
                return
    finally:
        # A PostgreSQL read starts a transaction. Close it promptly so test
        # schema resets and administrative operations are never lock-blocked.
        conn.close()
    raise HTTPException(
        status_code=403,
        detail=f"Permission denied: {' or '.join(permission_names)}",
    )


# ── Schema seed ─────────────────────────────────────────────────────────────

RBAC_DDL = """
CREATE TABLE IF NOT EXISTS webclaw_permission (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    description     TEXT,
    scope           TEXT NOT NULL DEFAULT 'tenant'
                    CHECK(scope IN ('platform','tenant')),
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS webclaw_permission_action (
    id              TEXT PRIMARY KEY,
    permission_id   TEXT NOT NULL REFERENCES webclaw_permission(id) ON DELETE CASCADE,
    skill           TEXT NOT NULL,
    action_pattern  TEXT NOT NULL,
    UNIQUE(permission_id, skill, action_pattern)
);

CREATE TABLE IF NOT EXISTS webclaw_role_grant (
    id              TEXT PRIMARY KEY,
    role_id         TEXT NOT NULL REFERENCES webclaw_role(id) ON DELETE CASCADE,
    permission_id   TEXT NOT NULL REFERENCES webclaw_permission(id) ON DELETE CASCADE,
    UNIQUE(role_id, permission_id)
);

CREATE INDEX IF NOT EXISTS idx_webclaw_permission_name ON webclaw_permission(name);
CREATE INDEX IF NOT EXISTS idx_webclaw_role_grant_role ON webclaw_role_grant(role_id);
CREATE INDEX IF NOT EXISTS idx_webclaw_perm_action_perm ON webclaw_permission_action(permission_id);
"""

RBAC_VIEWS = """
CREATE VIEW IF NOT EXISTS roles AS
    SELECT id, code, name, description, is_system, created_at FROM webclaw_role;

CREATE VIEW IF NOT EXISTS permissions AS
    SELECT id, name, description, scope, created_at FROM webclaw_permission;

CREATE VIEW IF NOT EXISTS user_roles AS
    SELECT id, user_id, role_id, tenant_id, created_at FROM webclaw_user_role;
"""


def migrate_rbac_schema(conn: DbConn) -> None:
    """Add code / tenant_id columns, catalog tables, and compatibility views."""
    from db_access import run_ddl

    run_ddl(conn, RBAC_DDL)

    role_cols = _table_cols(conn, "webclaw_role")
    if "code" not in role_cols:
        try:
            conn.execute("ALTER TABLE webclaw_role ADD COLUMN code TEXT")
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_webclaw_role_code "
            "ON webclaw_role(code) WHERE code IS NOT NULL"
        )
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass

    ur_cols = _table_cols(conn, "webclaw_user_role")
    if "tenant_id" not in ur_cols:
        try:
            conn.execute("ALTER TABLE webclaw_user_role ADD COLUMN tenant_id TEXT")
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_webclaw_user_role_tenant "
            "ON webclaw_user_role(tenant_id)"
        )
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass

    try:
        from tenant import ensure_user_tenant_table
        ensure_user_tenant_table(conn)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass

    # Views need the new columns; recreate if a previous empty view exists.
    for view in ("roles", "permissions", "user_roles"):
        try:
            conn.execute(f"DROP VIEW IF EXISTS {view}")
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
    try:
        run_ddl(conn, RBAC_VIEWS)
    except Exception:
        # Older DBs mid-migration may lack code/tenant_id; skip views.
        try:
            conn.rollback()
        except Exception:
            pass
    conn.commit()


def seed_rbac(conn: DbConn) -> None:
    """Idempotent seed of the permission catalog and PMS system roles."""
    migrate_rbac_schema(conn)

    for name, spec in PERMISSION_CATALOG.items():
        row = conn.execute(
            "SELECT id FROM webclaw_permission WHERE name = ?", (name,)
        ).fetchone()
        if row:
            perm_id = row[0]
            conn.execute(
                "UPDATE webclaw_permission SET description = ?, scope = ? WHERE id = ?",
                (spec["description"], spec["scope"], perm_id),
            )
        else:
            perm_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO webclaw_permission (id, name, description, scope)
                   VALUES (?, ?, ?, ?)""",
                (perm_id, name, spec["description"], spec["scope"]),
            )
        for skill, pattern in spec["actions"]:
            exists = conn.execute(
                """SELECT 1 FROM webclaw_permission_action
                   WHERE permission_id = ? AND skill = ? AND action_pattern = ?""",
                (perm_id, skill, pattern),
            ).fetchone()
            if not exists:
                conn.execute(
                    """INSERT INTO webclaw_permission_action
                       (id, permission_id, skill, action_pattern)
                       VALUES (?, ?, ?, ?)""",
                    (str(uuid.uuid4()), perm_id, skill, pattern),
                )

    perm_ids = {row[0]: row[1] for row in conn.execute("SELECT name, id FROM webclaw_permission")}

    for code in ROLE_CODES:
        display = ROLE_DISPLAY[code]
        desc = ROLE_DESCRIPTIONS[code]
        row = conn.execute(
            "SELECT id FROM webclaw_role WHERE name = ? OR code = ?",
            (display, code),
        ).fetchone()
        if row:
            role_id = row[0]
            conn.execute(
                """UPDATE webclaw_role
                   SET name = ?, code = ?, description = ?, is_system = 1
                   WHERE id = ?""",
                (display, code, desc, role_id),
            )
        else:
            role_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO webclaw_role (id, name, code, description, is_system)
                   VALUES (?, ?, ?, ?, 1)""",
                (role_id, display, code, desc),
            )

        for perm_name in ROLE_PERMISSIONS[code]:
            pid = perm_ids.get(perm_name)
            if not pid:
                continue
            exists = conn.execute(
                "SELECT 1 FROM webclaw_role_grant WHERE role_id = ? AND permission_id = ?",
                (role_id, pid),
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO webclaw_role_grant (id, role_id, permission_id) VALUES (?, ?, ?)",
                    (str(uuid.uuid4()), role_id, pid),
                )

        # Drop stale named grants so role scope matches ROLE_PERMISSIONS exactly.
        desired_perm_ids = {perm_ids[n] for n in ROLE_PERMISSIONS[code] if n in perm_ids}
        if desired_perm_ids:
            placeholders = ",".join("?" * len(desired_perm_ids))
            conn.execute(
                f"""DELETE FROM webclaw_role_grant
                    WHERE role_id = ?
                      AND permission_id NOT IN ({placeholders})""",
                (role_id, *desired_perm_ids),
            )
        else:
            conn.execute(
                "DELETE FROM webclaw_role_grant WHERE role_id = ?", (role_id,)
            )

        if code == FRONT_DESK:
            # Drop historical wildcards that leaked housekeeping + ledger.
            conn.execute(
                """DELETE FROM webclaw_role_permission
                   WHERE role_id = ? AND skill = 'hospitalityclaw'
                     AND action_pattern IN ('list-*', 'get-*', 'add-*', 'update-*', 'hospitality-*')""",
                (role_id,),
            )

        if code in (ACCOUNTANT, HOTEL_ACCOUNTANT):
            # Accountants must not keep check-in/out or booking wildcards from older seeds.
            conn.execute(
                """DELETE FROM webclaw_role_permission
                   WHERE role_id = ?
                     AND (
                       action_pattern IN (
                         'check-in', 'check-out', 'check-*',
                         'hospitality-check-in', 'hospitality-check-out',
                         'add-reservation', 'hospitality-add-reservation',
                         'assign-room', 'hospitality-assign-room'
                       )
                       OR (skill = 'hospitalityclaw' AND action_pattern IN (
                         'list-*', 'get-*', 'add-*', 'update-*', 'hospitality-*'
                       ))
                     )""",
                (role_id,),
            )

        desired_patterns = {
            (skill, pattern) for skill, pattern in ROLE_SKILL_PATTERNS.get(code, ())
        }
        if desired_patterns and code in (
            FRONT_DESK,
            ACCOUNTANT,
            HOTEL_ACCOUNTANT,
            HOUSEKEEPING,
        ):
            # Exact-sync skill globs for tightly scoped roles.
            rows = conn.execute(
                """SELECT id, skill, action_pattern FROM webclaw_role_permission
                   WHERE role_id = ?""",
                (role_id,),
            ).fetchall()
            for row in rows:
                rid, skill, pattern = row[0], row[1], row[2]
                if (skill, pattern) not in desired_patterns:
                    conn.execute(
                        "DELETE FROM webclaw_role_permission WHERE id = ?", (rid,)
                    )

        for skill, pattern in ROLE_SKILL_PATTERNS.get(code, ()):
            exists = conn.execute(
                """SELECT 1 FROM webclaw_role_permission
                   WHERE role_id = ? AND skill = ? AND action_pattern = ?""",
                (role_id, skill, pattern),
            ).fetchone()
            if not exists:
                conn.execute(
                    """INSERT INTO webclaw_role_permission
                       (id, role_id, skill, action_pattern, allowed)
                       VALUES (?, ?, ?, ?, 1)
                       ON CONFLICT (role_id, skill, action_pattern) DO NOTHING""",
                    (str(uuid.uuid4()), role_id, skill, pattern),
                )

    # Legacy Viewer + Hotel Staff (not in the six PMS codes).
    _ensure_legacy_role(
        conn, "Viewer", "Read-only access",
        [("*", "list-*"), ("*", "get-*")],
    )
    _ensure_legacy_role(
        conn, "Hotel Staff", "Legacy hotel read-only staff",
        [("hospitalityclaw", "list-*"), ("hospitalityclaw", "get-*")],
    )
    conn.commit()


def _ensure_legacy_role(conn, name, description, patterns):
    row = conn.execute("SELECT id FROM webclaw_role WHERE name = ?", (name,)).fetchone()
    if not row:
        role_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO webclaw_role (id, name, description, is_system) VALUES (?, ?, ?, 1)",
            (role_id, name, description),
        )
    else:
        role_id = row[0]
    count = conn.execute(
        "SELECT COUNT(*) FROM webclaw_role_permission WHERE role_id = ?", (role_id,)
    ).fetchone()[0]
    if count == 0:
        for skill, pattern in patterns:
            conn.execute(
                """INSERT INTO webclaw_role_permission
                   (id, role_id, skill, action_pattern, allowed) VALUES (?, ?, ?, ?, 1)""",
                (str(uuid.uuid4()), role_id, skill, pattern),
            )
    return role_id
