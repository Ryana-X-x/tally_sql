# TallyPrime Integration

## Overview

This application integrates with TallyPrime via two mechanisms:

1. **HTTP/XML (primary)** — TallyPrime's built-in HTTP server (port 9000)
2. **ODBC (alternative)** — Tally ODBC driver (Windows-only)

The integration layer is abstracted so the migration pipeline has no knowledge
of the transport mechanism.

---

## XML/HTTP Integration (Recommended)

### How It Works

TallyPrime includes a built-in HTTP server that accepts TDL (Tally Definition
Language) XML requests and returns XML responses.

```
Python                          TallyPrime
  │                                │
  │  POST http://host:9000         │
  │  Content-Type: application/xml │
  │  <ENVELOPE>                    │
  │    <HEADER>...</HEADER>        │
  │    <BODY>                      │
  │      <DESC>                    │
  │        <TDL>...</TDL>          │
  │      </DESC>                   │
  │    </BODY>                     │
  │  </ENVELOPE>                   │
  │ ─────────────────────────────► │
  │                                │
  │  <ENVELOPE>                    │
  │    <BODY>                      │
  │      <DATA>                    │
  │        <LEDGER>                │
  │          <GUID>...</GUID>      │
  │          <NAME>...</NAME>      │
  │        </LEDGER>               │
  │      </DATA>                   │
  │    </BODY>                     │
  │  </ENVELOPE>                   │
  │ ◄───────────────────────────── │
```

### Enabling in TallyPrime

1. Open TallyPrime
2. Go to: `Help > TDL & Add-On > F12: Configure`
3. Enable: **HTTP Server** (or via `Gateway of Tally > F12 > Advanced`)
4. Set port (default: 9000)
5. Note: Restart Tally after changing HTTP settings

### Configuration

```dotenv
TALLY_PROTOCOL=xml
TALLY_HOST=<tally-server-hostname-or-ip>
TALLY_PORT=9000
TALLY_COMPANY=<company-name>  # optional
```

---

## ODBC Integration (Alternative)

### How It Works

TallyPrime exposes an ODBC interface that makes Tally collections appear as
SQL-queryable tables.

```
Python (pyodbc)
   │
   │  ODBC connection
   ▼
Tally ODBC Driver (Windows)
   │
   │  Tally internal protocol
   ▼
TallyPrime process
```

### Limitations of ODBC

- **Windows-only**: The Tally ODBC driver is only available on Windows
- **Read-only**: ODBC provides read-only access to Tally data
- **Limited filtering**: Not all TDL filter expressions are supported
- **Slower for large datasets**: Row-by-row fetching may be slower than XML
- **Voucher sub-entries**: May not be accessible the same way as via XML

### Enabling in TallyPrime

1. Open TallyPrime
2. Go to: `Gateway of Tally > F12 > Advanced Configuration`
3. Enable: **ODBC Server**
4. Set ODBC port (default: 9000 — can be same as HTTP if both enabled)

### Configuring ODBC DSN (Windows)

1. Open **Windows ODBC Data Source Administrator** (32-bit or 64-bit)
2. Add a new **System DSN**
3. Select **Tally ODBC Driver**
4. Set `Server=localhost` and `Port=9000`
5. Name the DSN (e.g., `TallyODBC`)

### Configuration

```dotenv
TALLY_PROTOCOL=odbc
TALLY_HOST=localhost
TALLY_PORT=9000
TALLY_ODBC_DSN=TallyODBC        # if using named DSN
TALLY_ODBC_DRIVER=Tally ODBC Driver  # if using DSN-less connection
```

---

## Known Tally Collections

| Collection | Category | Key Field | Change Marker |
|---|---|---|---|
| Ledger | Master | GUID | ALTERID |
| Group | Master | GUID | ALTERID |
| StockItem | Master | GUID | ALTERID |
| StockGroup | Master | GUID | ALTERID |
| VoucherType | Master | GUID | ALTERID |
| Currency | Master | GUID | ALTERID |
| CostCentre | Master | GUID | ALTERID |
| CostCategory | Master | GUID | ALTERID |
| Godown | Master | GUID | ALTERID |
| Unit | Master | GUID | ALTERID |
| Voucher | Transaction | GUID | ALTERID |

---

## ALTERID Change Detection

Tally maintains an `ALTERID` field on all master and transaction records.
This is a monotonically increasing integer that increments whenever a record
is modified.

The incremental sync uses:

```
Fetch records where ALTERID > <last_known_alterid>
```

This is the most reliable change detection mechanism available in standard
Tally interfaces.

**Important**: ALTERID values are per-company and per-instance. If Tally
company data is rebuilt or restored from backup, ALTERID values may reset.
In this case, run a full sync.

---

## Custom TDL Fields

TallyPrime can be extended with custom fields via TDL (Tally Definition
Language). These may not appear in the standard field lists.

The discovery process will attempt to identify custom fields by:
1. Comparing discovered fields against the known standard field list
2. Sampling actual records and inspecting response keys
3. Flagging fields not present in the approved schema

Custom TDL fields are **reported** during `discover` and `validate-schema`.
They are **not silently discarded** and **not automatically added** to SQL.

The intended workflow for custom fields:
1. Run `discover` — custom fields appear as `is_custom_tdl: true`
2. Review the discovered schema
3. Manually add SQL columns for required custom fields
4. Re-run `generate-schema` and `validate-schema`

---

## Deletion Handling

TallyPrime standard APIs **do not expose deleted record identifiers**.

This means:
- Deleted Tally records are **not automatically removed** from SQL Server
- The incremental sync cannot detect deletions

The `DeletionReconciler` class provides periodic reconciliation:
1. Fetch all GUIDs from Tally (full fetch)
2. Fetch all GUIDs from SQL Server
3. GUIDs in SQL but not in Tally = candidates for deletion
4. Apply soft-delete (`_is_deleted = 1`) — not hard delete

This is a maintenance operation and is **not run automatically**.

---

## Client Factory

The `create_tally_client()` factory function selects the appropriate client:

```python
from tally_migrator.tally.factory import create_tally_client
from tally_migrator.config import get_config

client = create_tally_client(get_config().tally)
# Returns XMLTallyClient or ODBCTallyClient depending on TALLY_PROTOCOL
```

The rest of the application uses only the `BaseTallyClient` interface.
