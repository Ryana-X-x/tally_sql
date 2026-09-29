"""SQL Server schema management utilities."""

from __future__ import annotations

import logging

from tally_migrator.sql.connection import SQLConnection

logger = logging.getLogger(__name__)


class SQLSchemaManager:
    """Utilities for inspecting and managing SQL Server schema."""

    def __init__(self, sql: SQLConnection, config):
        self.sql = sql
        self.config = config
        self._schema = config.sql.schema_name

    def get_all_tables(self) -> list[str]:
        cursor = self.sql.cursor()
        cursor.execute(
            "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = ? AND TABLE_TYPE = 'BASE TABLE' "
            "ORDER BY TABLE_NAME",
            (self._schema,),
        )
        result = [row[0] for row in cursor.fetchall()]
        cursor.close()
        return result

    def get_table_columns(self, table_name: str) -> dict[str, str]:
        """Return {column_name: data_type} for a table."""
        cursor = self.sql.cursor()
        cursor.execute(
            "SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? ORDER BY ORDINAL_POSITION",
            (self._schema, table_name),
        )
        result = {row[0]: row[1] for row in cursor.fetchall()}
        cursor.close()
        return result

    def get_row_count(self, table_name: str) -> int:
        try:
            cursor = self.sql.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM [{self._schema}].[{table_name}]")
            count = cursor.fetchone()[0]
            cursor.close()
            return count
        except Exception:
            return -1

    def print_database_summary(self) -> None:
        tables = self.get_all_tables()
        print(f"\nDatabase: {self.config.sql.database} (schema: {self._schema})")
        print(f"Tables: {len(tables)}")
        print()
        print(f"{'TABLE':<45} {'ROWS':>12}")
        print("-" * 60)
        total = 0
        for t in tables:
            count = self.get_row_count(t)
            total += max(count, 0)
            count_str = f"{count:,}" if count >= 0 else "error"
            print(f"{t:<45} {count_str:>12}")
        print("-" * 60)
        print(f"{'TOTAL':<45} {total:>12,}")
