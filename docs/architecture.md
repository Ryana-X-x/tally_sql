# TallySQL Migrator — Architecture

## Overview

TallySQL Migrator is a Python application that transfers data **directly** from
TallyPrime into Microsoft SQL Server.

```
 TALLYPRIME
    │
    │  HTTP/XML (port 9000)   OR   ODBC (port 9000)
    ▼
 PYTHON MIGRATION ENGINE
 (bounded batch processing in memory)
    │
    │  pyodbc
    ▼
 MICROSOFT SQL SERVER
    └── TallyDB
```

There are **no intermediate files** in the migration path. No CSV, no `.data`
files, no extracted JSON dumps. The Python process reads a bounded batch of
records from Tally, transforms them in memory, and writes them directly to
SQL Server.

---

## Component Map

```
tally_migrator/
├── cli.py              CLI entry point and command dispatcher
├── config.py           Environment-based configuration
├── exceptions.py       Custom exception hierarchy
│
├── tally/
│   ├── base_client.py  Abstract Tally client interface
│   ├── xml_client.py   HTTP/TDL XML client (primary)
│   ├── odbc_client.py  ODBC client (Windows-only alternative)
│   ├── factory.py      Client factory (xml/odbc selection)
│   ├── discovery.py    Live Tally interrogation
│   └── models.py       Tally data models
│
├── schema/
│   ├── models.py       Schema representation (DiscoveredSchema)
│   ├── discovery.py    Schema discovery -> discovered_schema.json
│   ├── generator.py    SQL DDL generator
│   └── validator.py    Schema drift detection
│
├── sql/
│   ├── connection.py   pyodbc connection management
│   ├── upsert.py       MERGE-based upsert engine
│   ├── sync_state.py   Sync state per collection
│   ├── lock.py         Process lock (prevents concurrent runs)
│   └── schema.py       SQL schema inspection utilities
│
├── migration/
│   ├── pipeline.py     Core orchestration pipeline
│   ├── full_sync.py    Full synchronisation runner
│   ├── incremental_sync.py  Incremental sync + deletion reconciler
│   ├── mapper.py       Tally field -> SQL column mapping
│   └── transforms.py   Type-safe data transformation functions
│
└── logging/
    └── logger.py       Structured logging, run IDs, summaries
```

---

## Data Flow (Full Sync)

```
1. CLI: python -m tally_migrator full-sync
        │
2. FullSyncRunner.run()
        │
3. MigrationPipeline._setup()
   ├── XMLTallyClient.connect()    — HTTP probe to Tally
   ├── SQLConnection()             — pyodbc connection to SQL Server
   └── MigrationMapper()          — load discovered_schema.json
        │
4. For each collection:
   ├── 4a. XMLTallyClient.fetch_all('Ledger')  -> record generator
   ├── 4b. Accumulate batch (up to BATCH_SIZE records)
   ├── 4c. transform_record()   — type-safe field transforms
   ├── 4d. CollectionMapping.to_sql_record()  — map to SQL columns
   ├── 4e. BatchUpsertEngine.upsert_batch()   — SQL Server MERGE
   ├── 4f. SyncStateManager.mark_success()    — update sync state
   └── 4g. Release batch memory, fetch next batch
        │
5. MigrationResult.print_summary()
```

## Data Flow (Incremental Sync)

```
1. CLI: python -m tally_migrator incremental-sync
        │
2. IncrementalSyncRunner.run()
        │
3. For each collection:
   ├── 3a. SyncStateManager.get_state() -> since_marker (ALTERID value)
   ├── 3b. XMLTallyClient.fetch_since('Ledger', since_marker)
   │         — Tally filter: ALTERID > last_known_alterid
   ├── 3c. Transform + map (same as full sync)
   ├── 3d. BatchUpsertEngine.upsert_batch() -> INSERT/UPDATE/skip
   └── 3e. SyncStateManager.mark_success(source_marker=max_alterid)
```

---

## Upsert Logic

SQL Server MERGE is used for atomic upsert:

```sql
MERGE [dbo].[ledger] AS tgt
USING (VALUES (?)) AS src (guid)
ON tgt.guid = src.guid
WHEN MATCHED AND (tgt.name <> src.name OR ...) THEN
    UPDATE SET tgt.name = src.name, ...
WHEN NOT MATCHED BY TARGET THEN
    INSERT (guid, name, ...) VALUES (src.guid, src.name, ...)
OUTPUT $action;
```

The OUTPUT $action clause returns 'INSERT' or 'UPDATE', allowing accurate
per-record statistics.

---

## Change Detection (Incremental Sync)

Tally uses `ALTERID` — a monotonically increasing integer that increments
every time a record is modified in the Tally company data.

The sync state table stores the **maximum ALTERID seen** per collection after
each successful sync. On the next incremental sync:

```
Fetch: ALTERID > last_known_alterid
```

This returns only records created or modified since the last sync.

### Limitations

- **Deletions**: Tally standard APIs do not expose deleted record identifiers.
  Soft-delete reconciliation (GUID comparison) is available via
  `DeletionReconciler` but must be run manually as a maintenance operation.
- **ALTERID resets**: If Tally company data is rebuilt from backup, ALTERID
  values may reset. This should trigger a manual full sync.
- **ODBC limitations**: The ODBC interface may not support all filter
  expressions. Fallback to full-fetch is implemented for affected collections.

---

## Process Locking

The `SQLProcessLock` class uses the `migration_lock` SQL Server table to
prevent concurrent migration runs. This is critical for Windows Task Scheduler
where the same job could be triggered while a previous run is still active.

Stale locks (older than `max_lock_age_minutes`) are automatically overridden.

---

## Schema Workflow

```
discover          -> discovers Tally schema, saves schema/discovered_schema.json
                     (schema METADATA only - no row data)
        │
review            -> human reviews/approves the schema
        │
generate-schema   -> generates sql/schema.sql DDL
        │
validate-schema   -> compares discovered schema vs actual SQL tables
        │
full-sync         -> migrates all data
        │
incremental-sync  -> syncs only changed records
```

---

## Memory Management

The pipeline never loads an entire collection into memory. Processing is:

```python
for raw_record in tally_client.fetch_all('Voucher'):   # generator
    batch.append(transform(raw_record))
    if len(batch) >= BATCH_SIZE:
        write_batch(batch)
        batch = []    # release memory
```

With `BATCH_SIZE=5000`, the maximum in-memory batch is approximately
5000 * (average record size). For most Tally collections this is manageable.

---

## SQL Server Design Decisions

### Normalised Voucher Schema

Tally vouchers are hierarchical. The SQL schema normalises them:

| SQL Table | Source | Relationship |
|---|---|---|
| `voucher` | Tally Voucher | Parent record |
| `voucher_ledger_entry` | AllLedgerEntries | FK -> voucher.guid |
| `voucher_inventory_entry` | AllInventoryEntries | FK -> voucher.guid |
| `voucher_bill_allocation` | BillAllocations | FK -> voucher.guid |
| `voucher_bank_entry` | BankAllocations | FK -> voucher.guid |
| `voucher_batch_allocation` | BatchAllocations | FK -> voucher.guid |

### Metadata Columns

Every SQL table includes:

| Column | Purpose |
|---|---|
| `_imported_at` | Timestamp when record was first imported |
| `_updated_at` | Timestamp of last update |
| `_run_id` | Run ID of the migration that last touched this record |

### Non-Destructive by Default

The application **never** issues DROP TABLE, DROP DATABASE, TRUNCATE TABLE, or
bulk DELETE statements. Schema changes are explicit and require the `--apply`
flag on `generate-schema`.
