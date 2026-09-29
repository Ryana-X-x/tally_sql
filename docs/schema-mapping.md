# Schema Mapping

## Overview

The schema mapping defines how Tally fields are mapped to SQL Server columns.
This is a two-step process:

1. **Discover** — interrogate live Tally to determine actual schema
2. **Map** — connect Tally fields to SQL columns with type information

---

## Tally Type to SQL Type Mapping

| Tally Field Type | SQL Server Type | Notes |
|---|---|---|
| guid | NVARCHAR(50) | Tally GUIDs are composite strings, not UUIDs |
| text | NVARCHAR(255) | Standard text fields |
| large_text | NVARCHAR(MAX) | Narration, description fields |
| date | DATE | Explicit conversion from YYYYMMDD |
| datetime | DATETIME2 | Where datetime resolution needed |
| amount | DECIMAL(20,4) | All monetary values |
| quantity | DECIMAL(20,4) | Inventory quantities |
| rate | DECIMAL(20,6) | Unit rates (higher precision) |
| logical | BIT | Yes/No booleans from Tally |
| integer | BIGINT | ALTERID, numeric counts |
| unknown | NVARCHAR(255) | Fallback for undetermined types |

---

## Date Handling

**Critical**: Tally dates require explicit parsing. SQL Server implicit
conversion has caused failures in previous integrations.

Supported input formats:
- `20231201` (YYYYMMDD — Tally native)
- `2023-12-01` (ISO 8601)
- `01/12/2023` (DD/MM/YYYY)
- `01-12-2023` (DD-MM-YYYY)

Null sentinels (treated as NULL):
- Empty string
- `00000000`
- `19000101` (Tally epoch)

---

## Amount Handling

Tally amounts may include Dr/Cr suffixes:

| Tally Value | Python Result | SQL |
|---|---|---|
| `1000.00` | Decimal('1000.00') | 1000.0000 |
| `1000.00 Dr` | Decimal('-1000.00') | -1000.0000 |
| `1000.00 Cr` | Decimal('1000.00') | 1000.0000 |
| `-500.00` | Decimal('-500.00') | -500.0000 |
| `1,234.56` | Decimal('1234.56') | 1234.5600 |
| `` (empty) | None | NULL |

---

## Collection to SQL Table Mapping

| Tally Collection | SQL Table | Primary Key | Change Marker |
|---|---|---|---|
| Ledger | ledger | guid | alterid |
| Group | group | guid | alterid |
| StockItem | stock_item | guid | alterid |
| StockGroup | stock_group | guid | alterid |
| VoucherType | voucher_type | guid | alterid |
| Currency | currency | guid | alterid |
| CostCentre | cost_centre | guid | alterid |
| CostCategory | cost_category | guid | alterid |
| Godown | godown | guid | alterid |
| Unit | unit | guid | alterid |
| Voucher | voucher | guid | alterid |

### Voucher Sub-Tables

| SQL Table | Source Collection/Sub | Foreign Key |
|---|---|---|
| voucher_ledger_entry | AllLedgerEntries | voucher_guid -> voucher.guid |
| voucher_inventory_entry | AllInventoryEntries | voucher_guid -> voucher.guid |
| voucher_bill_allocation | BillAllocations | voucher_guid -> voucher.guid |
| voucher_bank_entry | BankAllocations | voucher_guid -> voucher.guid |
| voucher_batch_allocation | BatchAllocations | voucher_guid -> voucher.guid |

---

## Identifier Strategy

All Tally master records have a `GUID` field. This is the preferred
primary key for SQL tables because:

1. **Stable** — the GUID is assigned by Tally and persists across backups
2. **Unique** — GUIDs are company-scoped unique identifiers
3. **Format** — `<CompanyID>-<ObjectID>` as a composite string

For SQL tables, the GUID is stored as NVARCHAR(50) and used as:
- The PRIMARY KEY (or part of a unique constraint)
- The upsert key for MERGE operations
- The foreign key for voucher sub-table relationships

---

## Unmapped Fields

When the discovery process finds fields not in the approved schema:

1. The field is added to `unmapped_fields` in `DiscoveredCollection`
2. The field is **not** automatically added to SQL
3. The field is **reported** during `validate-schema`
4. The migration **skips** the field (does not write to SQL) but **logs** a warning

This prevents silent data loss while also preventing uncontrolled schema changes.

---

## Custom TDL Field Workflow

```
1. python -m tally_migrator discover
   -> discovered_schema.json contains is_custom_tdl: true fields

2. Review schema/discovered_schema.json
   -> Identify which custom fields are needed

3. Edit the SQL schema manually to add custom columns
   -> Or modify the discovered_schema.json to mark fields as mapped

4. python -m tally_migrator generate-schema
   -> Regenerate DDL

5. python -m tally_migrator validate-schema
   -> Verify schema matches

6. python -m tally_migrator full-sync
   -> Migrate all data including custom fields
```

---

## Existing Database Compatibility

The existing `TallyDB` database (with ~811,541 rows across ~16 tables)
is preserved. The system:

- Never issues DROP TABLE or TRUNCATE without explicit confirmation
- Creates new tables alongside existing ones
- Uses IF NOT EXISTS for all DDL
- Reports conflicts between old and new schema via `validate-schema`
- Provides configurable schema/table names via `SQL_SCHEMA` environment variable
