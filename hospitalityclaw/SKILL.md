---
name: hospitalityclaw
version: 0.1.0
description: >
  Hotel property management: rooms, guests, reservations, folios, and
  folio ledger. Row-isolated by tenant_id (the ERP company UUID).
author: PMS
category: hospitality
requires: [erpclaw]
# Runtime DB is PostgreSQL (DB_TYPE=postgres → schema public).
# ERPCLAW_DB_PATH is a migrate-from-SQLite source path only.
database: "${ERPCLAW_DB_URL}"
user-invocable: true
tags: [hotel, pms, reservations, rooms, folio]
webclaw:
  domain: "Hospitality"
  category: hospitality
  database: "${ERPCLAW_DB_URL}"
  entities:
    hotel:
      table: hospitalityclaw_hotel
      name_col: name
      id_col: id
      search_cols: [name, country]
    guest:
      table: hospitalityclaw_guest
      name_col: last_name
      id_col: id
      search_cols: [first_name, last_name, email]
    reservation:
      table: hospitalityclaw_reservation
      name_col: id
      id_col: id
      search_cols: [status, check_in]
---

# hospitalityclaw

Hotel operations for a multi-tenant PMS. Every row carries `tenant_id`
equal to the hotel's ERP `company.id`. The API binds this from the JWT;
do not accept a client-supplied tenant for hotel staff.

## Actions

| Action | Args | Description |
|--------|------|-------------|
| `hospitality-initialize-schema` | — | Create PMS tables |
| `hospitality-seed-defaults` | `--hotel-name`, `--timezone`, `--currency`, `--country` | Room types + tax template |
| `hospitality-status` | `--from-date`, `--to-date` | Counts for this hotel (optional date window) |
| `hospitality-list-hotels` | — | Properties in this tenant |
| `hospitality-get-hotel` | `--id` | One property |
| `hospitality-update-hotel` | `--id`, fields | Update property |
| `hospitality-add-room-type` | `--hotel-id`, `--name`, `--code`, `--base-rate` | Add room type |
| `hospitality-list-room-types` | — | List room types |
| `hospitality-update-room-type` | `--id`, fields | Update room type |
| `hospitality-delete-room-type` | `--id` | Delete unused room type |
| `hospitality-add-room` | `--hotel-id`, `--room-type-id`, `--room-number` | Add room |
| `hospitality-list-rooms` | `--hotel-id`, `--status` | List rooms |
| `hospitality-update-room` | `--id`, fields | Update room number / type / floor |
| `hospitality-delete-room` | `--id` | Delete unused room |
| `hospitality-update-room-status` | `--id`, `--status` | Housekeeping: Clean / Dirty / Maintenance |
| `hospitality-add-guest` | `--first-name`, `--last-name` | Add guest |
| `hospitality-list-guests` | `--email` | List guests |
| `hospitality-get-guest` | `--id` | One guest |
| `hospitality-add-reservation` | `--hotel-id`, `--guest-id`, `--room-type-id`, `--check-in`, `--check-out` | Book a stay |
| `hospitality-assign-room` | `--id`, `--room-id` | Assign a room to a reserved stay |
| `hospitality-cancel-reservation` | `--id`, optional `--notes` | Cancel a reserved stay before check-in |
| `hospitality-list-reservations` | `--status` | List bookings |
| `hospitality-get-reservation` | `--id` | One booking |
| `hospitality-check-in` | `--id`, `--room-id` | Check in + room charge |
| `hospitality-check-out` | `--id` | Check out (folio must be settled) |
| `hospitality-add-folio-charge` | `--folio-id`, `--description`, `--amount`, `--charge-type` | Charge or payment |
| `hospitality-list-folio-charges` | `--folio-id` | Folio lines |
| `hospitality-settle-folio` | `--folio-id` | Pay remaining balance |
| `hospitality-post-room-tariff` | `--folio-id` or all in-house | Nightly room rate → folio + GL |
| `hospitality-add-rate-plan` | `--hotel-id`, `--name`, `--code`, `--start-date`, `--end-date`, `--nightly-rate` | Seasonal rate plan |
| `hospitality-list-rate-plans` | — | List rate plans |
| `hospitality-update-rate-plan` | `--id`, fields | Update rate plan |
| `hospitality-delete-rate-plan` | `--id` | Delete rate plan |
| `hospitality-set-rate-window` | `--hotel-id`, `--start-date`, `--end-date`, `--nightly-rate`, optional `--room-type-id` | Upsert a month / date-range rate (auto priority) |
| `hospitality-rate-calendar` | `--hotel-id`, `--room-type-id`, `--start-date`, `--end-date` | Resolved nightly rates for a date window |
| `hospitality-add-tax-rule` | `--hotel-id`, `--name`, `--rate-percent`, `--applies-to` | Occupancy / F&B tax rule |
| `hospitality-list-tax-rules` | — | List tax rules |
| `hospitality-update-tax-rule` | `--id`, fields | Update tax rule |
| `hospitality-delete-tax-rule` | `--id` | Delete tax rule |
| `hospitality-list-ledger-entries` | `--folio-id` | Guest ledger |
| `hospitality-list-transactions` | `--folio-id` | Payments |

## Isolation

Hotel staff never see another tenant's rooms, guests, or folios. SuperAdmin
(System Manager) may pass `--company-id` to inspect a hotel.
