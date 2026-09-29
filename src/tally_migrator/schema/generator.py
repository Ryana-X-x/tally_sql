"""SQL schema generator.

Converts a DiscoveredSchema into Microsoft SQL Server DDL.

The generated schema:
- Uses appropriate SQL Server types
- Normalises nested Tally structures into related tables
- Includes primary keys, foreign keys, and indexes
- Is safe to review before applying
- Is never applied automatically without --apply flag
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from tally_migrator.schema.models import DiscoveredCollection, DiscoveredSchema

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hardcoded normalised schema for voucher sub-tables
# Tally vouchers are hierarchical - this defines the relational SQL layout
# ---------------------------------------------------------------------------

VOUCHER_SUB_TABLES = {
    "voucher_ledger_entry": {
        "description": "Ledger entries (debit/credit legs) within each voucher",
        "columns": [
            ("id", "BIGINT IDENTITY(1,1)", False, True),  # (name, type, nullable, pk)
            ("voucher_guid", "NVARCHAR(64)", False, False),
            ("ledger_name", "NVARCHAR(255)", False, False),
            ("amount", "DECIMAL(20,4)", True, False),
            ("is_party", "BIT", True, False),
            ("currency_name", "NVARCHAR(50)", True, False),
            ("forex_amount", "DECIMAL(20,4)", True, False),
            ("entry_type", "NVARCHAR(10)", True, False),
            ("gst_class", "NVARCHAR(100)", True, False),
        ],
        "fk": ("voucher_guid", "voucher", "guid"),
        "indexes": ["voucher_guid", "ledger_name"],
    },
    "voucher_inventory_entry": {
        "description": "Inventory (stock) entries within each voucher",
        "columns": [
            ("id", "BIGINT IDENTITY(1,1)", False, True),
            ("voucher_guid", "NVARCHAR(64)", False, False),
            ("stock_item_name", "NVARCHAR(255)", False, False),
            ("quantity", "DECIMAL(20,4)", True, False),
            ("rate", "DECIMAL(20,6)", True, False),
            ("amount", "DECIMAL(20,4)", True, False),
            ("uom", "NVARCHAR(50)", True, False),
            ("godown_name", "NVARCHAR(255)", True, False),
            ("batch_name", "NVARCHAR(255)", True, False),
            ("tracking_number", "NVARCHAR(100)", True, False),
        ],
        "fk": ("voucher_guid", "voucher", "guid"),
        "indexes": ["voucher_guid", "stock_item_name"],
    },
    "voucher_bill_allocation": {
        "description": "Bill/reference allocations within each voucher",
        "columns": [
            ("id", "BIGINT IDENTITY(1,1)", False, True),
            ("voucher_guid", "NVARCHAR(64)", False, False),
            ("bill_name", "NVARCHAR(255)", False, False),
            ("bill_type", "NVARCHAR(50)", True, False),
            ("amount", "DECIMAL(20,4)", True, False),
            ("due_date", "DATE", True, False),
        ],
        "fk": ("voucher_guid", "voucher", "guid"),
        "indexes": ["voucher_guid"],
    },
    "voucher_bank_entry": {
        "description": "Bank/instrument entries within each voucher",
        "columns": [
            ("id", "BIGINT IDENTITY(1,1)", False, True),
            ("voucher_guid", "NVARCHAR(64)", False, False),
            ("instrument_date", "DATE", True, False),
            ("instrument_number", "NVARCHAR(100)", True, False),
            ("bank_name", "NVARCHAR(255)", True, False),
            ("amount", "DECIMAL(20,4)", True, False),
        ],
        "fk": ("voucher_guid", "voucher", "guid"),
        "indexes": ["voucher_guid"],
    },
    "voucher_batch_allocation": {
        "description": "Batch/lot allocations within each voucher inventory entry",
        "columns": [
            ("id", "BIGINT IDENTITY(1,1)", False, True),
            ("voucher_guid", "NVARCHAR(64)", False, False),
            ("batch_name", "NVARCHAR(255)", True, False),
            ("destination_godown", "NVARCHAR(255)", True, False),
            ("quantity", "DECIMAL(20,4)", True, False),
            ("amount", "DECIMAL(20,4)", True, False),
            ("manufactured_on", "DATE", True, False),
            ("expiry_period", "NVARCHAR(50)", True, False),
        ],
        "fk": ("voucher_guid", "voucher", "guid"),
        "indexes": ["voucher_guid"],
    },
}


class SQLSchemaGenerator:
    """Generates SQL Server DDL from a discovered Tally schema."""

    def __init__(self, schema_path: Path, config):
        self.schema_path = schema_path
        self.config = config
        self._schema: Optional[DiscoveredSchema] = None

    def _load_schema(self) -> DiscoveredSchema:
        if self._schema is None:
            from tally_migrator.schema.discovery import SchemaDiscovery
            self._schema = SchemaDiscovery.load(self.schema_path)
        return self._schema

    def generate(self) -> str:
        """Generate complete SQL DDL string."""
        schema = self._load_schema()
        schema_name = self.config.sql.schema_name

        parts = [
            self._header(),
            self._create_sync_state_table(schema_name),
            self._create_migration_lock_table(schema_name),
        ]

        # Master tables first
        master_collections = [
            c for c in schema.collections.values()
            if c.category.value == "master"
        ]
        transaction_collections = [
            c for c in schema.collections.values()
            if c.category.value == "transaction"
        ]
        other_collections = [
            c for c in schema.collections.values()
            if c.category.value not in ("master", "transaction")
        ]

        parts.append("-- =" * 35)
        parts.append("-- MASTER TABLES")
        parts.append("-- =" * 35)
        for coll in master_collections:
            parts.append(self._create_collection_table(coll, schema_name))

        parts.append("-- =" * 35)
        parts.append("-- TRANSACTION TABLES")
        parts.append("-- =" * 35)
        for coll in transaction_collections:
            parts.append(self._create_collection_table(coll, schema_name))

        # Voucher sub-tables
        parts.append("-- =" * 35)
        parts.append("-- VOUCHER DETAIL TABLES (normalised sub-tables)")
        parts.append("-- =" * 35)
        parts.append(self._create_voucher_sub_tables(schema_name))

        for coll in other_collections:
            parts.append(self._create_collection_table(coll, schema_name))

        return "\n".join(parts)

    def _header(self) -> str:
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat()
        return (
            f"-- TallyPrime → SQL Server Schema\n"
            f"-- Generated: {ts}\n"
            f"-- Source: {self.schema_path}\n"
            f"-- DO NOT EDIT MANUALLY - regenerate via: python -m tally_migrator generate-schema\n"
            f"--\n"
            f"-- Apply to SQL Server via: python -m tally_migrator generate-schema --apply\n"
            f"--\n"
            f"SET NOCOUNT ON;\n"
            f"SET QUOTED_IDENTIFIER ON;\n\n"
        )

    def _create_sync_state_table(self, schema_name: str) -> str:
        return f"""
-- Synchronisation state table
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'[{schema_name}].[migration_sync_state]') AND type = 'U')
BEGIN
    CREATE TABLE [{schema_name}].[migration_sync_state] (
        [id]                    INT IDENTITY(1,1)       NOT NULL,
        [collection_name]       NVARCHAR(100)           NOT NULL,
        [last_successful_sync]  DATETIME2               NULL,
        [source_marker]         NVARCHAR(255)           NULL,
        [last_run_id]           NVARCHAR(50)            NULL,
        [status]                NVARCHAR(20)            NOT NULL DEFAULT 'pending',
        [rows_read]             BIGINT                  NOT NULL DEFAULT 0,
        [rows_inserted]         BIGINT                  NOT NULL DEFAULT 0,
        [rows_updated]          BIGINT                  NOT NULL DEFAULT 0,
        [rows_deleted]          BIGINT                  NOT NULL DEFAULT 0,
        [rows_failed]           BIGINT                  NOT NULL DEFAULT 0,
        [error_message]         NVARCHAR(MAX)           NULL,
        [created_at]            DATETIME2               NOT NULL DEFAULT SYSUTCDATETIME(),
        [updated_at]            DATETIME2               NOT NULL DEFAULT SYSUTCDATETIME(),
        CONSTRAINT [PK_migration_sync_state] PRIMARY KEY ([id]),
        CONSTRAINT [UQ_migration_sync_state_collection] UNIQUE ([collection_name])
    );
END
GO
"""

    def _create_migration_lock_table(self, schema_name: str) -> str:
        return f"""
-- Migration process lock table (prevents concurrent runs)
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'[{schema_name}].[migration_lock]') AND type = 'U')
BEGIN
    CREATE TABLE [{schema_name}].[migration_lock] (
        [lock_name]     NVARCHAR(100)   NOT NULL,
        [locked_at]     DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME(),
        [locked_by]     NVARCHAR(255)   NULL,
        [run_id]        NVARCHAR(50)    NULL,
        CONSTRAINT [PK_migration_lock] PRIMARY KEY ([lock_name])
    );
END
GO
"""

    def _create_collection_table(self, coll: DiscoveredCollection, schema_name: str) -> str:
        table_name = coll.sql_table_name or coll.name.lower()
        lines = [
            f"-- Source: Tally '{coll.name}' collection ({coll.category.value})",
            f"IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'[{schema_name}].[{table_name}]') AND type = 'U')",
            "BEGIN",
            f"    CREATE TABLE [{schema_name}].[{table_name}] (",
        ]

        col_defs = []
        pk_fields = []

        # Always add a surrogate PK if no GUID PK
        has_guid_pk = any(
            f.is_primary_key and f.tally_type.value == "guid"
            for f in coll.fields.values()
        )

        if not has_guid_pk and not coll.fields:
            col_defs.append("        [id] BIGINT IDENTITY(1,1) NOT NULL")
            pk_fields.append("id")

        for field_name, field_def in coll.fields.items():
            sql_col = _to_sql_column_name(field_name)
            nullable = "NULL" if field_def.nullable else "NOT NULL"
            col_defs.append(f"        [{sql_col}] {field_def.sql_type or 'NVARCHAR(255)'} {nullable}")
            if field_def.is_primary_key:
                pk_fields.append(sql_col)

        # Always add metadata columns
        col_defs.append("        [_imported_at] DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()")
        col_defs.append("        [_updated_at] DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()")
        col_defs.append("        [_run_id] NVARCHAR(50) NULL")

        lines.append(",\n".join(col_defs))

        if pk_fields:
            pk_name = f"PK_{table_name}"
            pk_cols = ", ".join(f"[{c}]" for c in pk_fields)
            lines.append(f"        ,CONSTRAINT [{pk_name}] PRIMARY KEY ({pk_cols})")

        lines.append("    );")
        lines.append("END")
        lines.append("GO")
        lines.append("")

        # Add indexes
        if coll.change_marker_field:
            idx_col = _to_sql_column_name(coll.change_marker_field)
            idx_name = f"IX_{table_name}_{idx_col}"
            lines.append(
                f"IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = '{idx_name}')\n"
                f"    CREATE INDEX [{idx_name}] ON [{schema_name}].[{table_name}] ([{idx_col}]);\n"
                f"GO"
            )

        return "\n".join(lines)

    def _create_voucher_sub_tables(self, schema_name: str) -> str:
        parts = []
        for table_name, defn in VOUCHER_SUB_TABLES.items():
            col_defs = []
            pk_cols = []
            for col_name, col_type, nullable, is_pk in defn["columns"]:
                null_str = "NULL" if nullable else "NOT NULL"
                col_defs.append(f"        [{col_name}] {col_type} {null_str}")
                if is_pk:
                    pk_cols.append(col_name)

            # metadata
            col_defs.append("        [_imported_at] DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()")
            col_defs.append("        [_updated_at] DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()")
            col_defs.append("        [_run_id] NVARCHAR(50) NULL")

            pk_str = ", ".join(f"[{c}]" for c in pk_cols)
            fk_col, fk_table, fk_ref_col = defn["fk"]

            block = (
                f"-- {defn['description']}\n"
                f"IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'[{schema_name}].[{table_name}]') AND type = 'U')\n"
                f"BEGIN\n"
                f"    CREATE TABLE [{schema_name}].[{table_name}] (\n"
                + ",\n".join(col_defs) + "\n"
                f"        ,CONSTRAINT [PK_{table_name}] PRIMARY KEY ({pk_str})\n"
                f"        ,CONSTRAINT [FK_{table_name}_{fk_col}] FOREIGN KEY ([{fk_col}]) REFERENCES [{schema_name}].[{fk_table}] ([{fk_ref_col}])\n"
                f"    );\n"
                f"END\nGO\n"
            )

            # Indexes
            for idx_col in defn.get("indexes", []):
                block += (
                    f"IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_{table_name}_{idx_col}')\n"
                    f"    CREATE INDEX [IX_{table_name}_{idx_col}] ON [{schema_name}].[{table_name}] ([{idx_col}]);\n"
                    f"GO\n"
                )

            parts.append(block)
        return "\n".join(parts)


def _to_sql_column_name(tally_field_name: str) -> str:
    """Convert Tally field name to SQL column name (lowercase)."""
    return tally_field_name.lower()
