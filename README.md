# TallyPrime → Microsoft SQL Server Migration & Sync Engine

A production-ready Python application for direct data migration and ongoing incremental synchronization from remote **TallyPrime** installations to **Microsoft SQL Server**.

---

## Key Features

- **Direct Memory Pipeline**: Streams data directly from Tally to SQL Server — zero CSV/intermediate file bottlenecks.
- **Dual Connection Modes**: Supports both Tally HTTP/XML TDL (primary, cross-platform) and Tally ODBC driver.
- **Automated Schema Discovery**: Discovers Tally master & transaction collections and maps TDL types to native SQL Server types.
- **Idempotent MERGE Upserts**: Uses SQL Server `MERGE` statements and `fast_executemany` for high-throughput, atomic inserts/updates.
- **Incremental Sync Engine**: Tracks change markers (`ALTERID` / `DATE`) to sync only modified records.
- **Distributed Lock Management**: SQL-backed process locking prevents concurrent overlapping runs in scheduled tasks.
- **Structured Operations**: Complete CLI with subcommands, environment file configuration, and detailed run logging.

---

## Architecture

```
┌─────────────────┐       XML/HTTP (Port 9000)       ┌────────────────────────┐       pyodbc / MERGE       ┌────────────────────┐
│   TallyPrime    │ ───────────────────────────────> │ TallySQL Migrator      │ ─────────────────────────> │   SQL Server DB    │
│  (Remote Host)  │     or Tally ODBC Driver         │ (Python Core Engine)   │     (Batch Upserts)        │  (Target Warehouse)│
└─────────────────┘                                  └────────────────────────┘                            └────────────────────┘
```

---

## Installation

### Prerequisites

- Python 3.10+
- Microsoft ODBC Driver 17 or 18 for SQL Server
- Remote or local TallyPrime installation with HTTP Server enabled (default port `9000`)

### Setup

```bash
# Clone repository & enter workspace
git clone https://github.com/example/tally_sql.git
cd tally_sql

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies in editable mode
pip install -e . -r requirements-dev.txt
```

---

## Configuration

Copy `.env.example` to `.env` and fill in your connection details:

```env
# Tally Configuration
TALLY_HOST=192.168.1.50
TALLY_PORT=9000
TALLY_PROTOCOL=xml
TALLY_COMPANY=Demo Company

# SQL Server Configuration
SQL_SERVER=sql-server-host.domain
SQL_DATABASE=TallyDB
SQL_USERNAME=tally_sync_user
SQL_PASSWORD=SecretPassword123!
SQL_DRIVER=ODBC Driver 17 for SQL Server
SQL_SCHEMA=dbo

# Migration Settings
BATCH_SIZE=5000
LOG_LEVEL=INFO
```

---

## CLI Usage

The CLI executable is `tally-migrator` (or `python -m tally_migrator`):

```bash
# 1. Verify Connectivity to Tally & SQL Server
tally-migrator test-connection

# 2. Discover Tally Schema
tally-migrator discover

# 3. Generate SQL Server DDL Script (and optionally apply it)
tally-migrator generate-schema --apply

# 4. Dry-run Migration (verify transformation & pipeline without writing to DB)
tally-migrator dry-run

# 5. Full Initial Data Migration
tally-migrator full-sync

# 6. Incremental Synchronization (Sync changes since last run)
tally-migrator incremental-sync

# 7. Check Synchronization Status Across Collections
tally-migrator status
```

---

## Automated Testing & Quality

Run the test suite with coverage:

```bash
pytest --cov=tally_migrator --cov-report=term-missing
```

Run code formatting & linting checks:

```bash
ruff check src tests
```

---

## License

MIT License.
