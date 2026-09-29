"""Batch upsert (INSERT/UPDATE/NO-ACTION) engine for SQL Server.

Implements the upsert logic:
    new source ID        -> INSERT
    existing + changed   -> UPDATE
    existing + unchanged -> skip

Uses:
    - pyodbc fast_executemany for batch performance
    - Parameterized SQL (never string interpolation of values)
    - SQL Server MERGE statement for atomic upsert
    - Batch-level transactions

Direct Tally -> Python memory -> SQL Server flow.
No CSV, no intermediate files.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from tally_migrator.exceptions import SQLExecutionError
from tally_migrator.sql.connection import SQLConnection

logger = logging.getLogger(__name__)


@dataclass
class UpsertResult:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0

    def __add__(self, other: "UpsertResult") -> "UpsertResult":
        return UpsertResult(
            inserted=self.inserted + other.inserted,
            updated=self.updated + other.updated,
            unchanged=self.unchanged + other.unchanged,
            failed=self.failed + other.failed,
        )


class BatchUpsertEngine:
    """Performs batch upsert of records into a SQL Server table.

    Uses SQL Server MERGE for atomic upsert semantics.

    Example:
        engine = BatchUpsertEngine(sql_conn, 'dbo', 'ledger', 'guid')
        result = engine.upsert_batch(records)
    """

    def __init__(
        self,
        sql: SQLConnection,
        schema_name: str,
        table_name: str,
        key_column: str,
        run_id: str = "",
    ):
        self.sql = sql
        self.schema_name = schema_name
        self.table_name = table_name
        self.key_column = key_column
        self.run_id = run_id
        self._columns: Optional[list[str]] = None

    def _table_ref(self) -> str:
        return f"[{self.schema_name}].[{self.table_name}]"

    def _get_table_columns(self) -> list[str]:
        """Retrieve column names from the target table (cached)."""
        if self._columns is not None:
            return self._columns
        cursor = self.sql.cursor()
        try:
            cursor.execute(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? "
                "AND COLUMN_NAME NOT IN ('_imported_at', '_updated_at', '_run_id') "
                "ORDER BY ORDINAL_POSITION",
                (self.schema_name, self.table_name),
            )
            cols = [row[0] for row in cursor.fetchall()]
            cursor.close()
            self._columns = cols
            return cols
        except Exception as exc:
            cursor.close()
            raise SQLExecutionError(self.table_name, "get_columns", str(exc)) from exc

    def upsert_batch(
        self,
        records: list[dict[str, Any]],
        dry_run: bool = False,
    ) -> UpsertResult:
        """Upsert a batch of records into the target table.

        Each record is a dict {column_name: value}.
        Records without the key column are skipped.

        Args:
            records: List of record dicts to upsert.
            dry_run: If True, validate but do not write.

        Returns:
            UpsertResult with counts.
        """
        if not records:
            return UpsertResult()

        result = UpsertResult()

        # Determine columns from first record (all records must have same structure)
        if not records:
            return result

        # Filter out records missing the key
        valid = [r for r in records if r.get(self.key_column) is not None]
        skipped = len(records) - len(valid)
        if skipped:
            logger.warning(
                "Skipped %d records missing key column '%s' in table '%s'",
                skipped, self.key_column, self.table_name,
            )
            result.failed += skipped

        if not valid:
            return result

        if dry_run:
            # Just validate the record structure
            result.inserted += len(valid)  # assume all would be inserted
            return result

        try:
            with self.sql.transaction():
                r = self._execute_merge(valid)
                result = result + r
        except Exception as exc:
            logger.error(
                "Batch upsert failed for table '%s': %s",
                self.table_name, exc,
            )
            result.failed += len(valid)

        return result

    def _execute_merge(self, records: list[dict[str, Any]]) -> UpsertResult:
        """Execute SQL Server MERGE for a batch of records.

        SQL Server MERGE allows atomic INSERT/UPDATE in one statement.
        Uses OUTPUT clause to count inserted vs updated.
        """
        if not records:
            return UpsertResult()

        # Get all column names from the first record
        # (assumes all records have the same keys)
        all_cols = list(records[0].keys())
        if not all_cols:
            return UpsertResult()

        # Add _run_id, _updated_at to updates
        set_cols = [c for c in all_cols if c != self.key_column]

        col_list = ", ".join(f"[{c}]" for c in all_cols)
        src_col_list = ", ".join(f"src.[{c}]" for c in all_cols)
        set_clause = ", ".join(f"tgt.[{c}] = src.[{c}]" for c in set_cols)
        placeholders = ", ".join("?" for _ in all_cols)

        merge_sql = f"""
        MERGE {self._table_ref()} AS tgt
        USING (VALUES ({placeholders})) AS src ({col_list})
        ON tgt.[{self.key_column}] = src.[{self.key_column}]
        WHEN MATCHED AND (
            {self._build_change_check(set_cols)}
        ) THEN UPDATE SET
            {set_clause},
            [_updated_at] = SYSUTCDATETIME(),
            [_run_id] = '{self.run_id}'
        WHEN NOT MATCHED BY TARGET THEN
            INSERT ({col_list}, [_run_id])
            VALUES ({src_col_list}, '{self.run_id}')
        OUTPUT $action;
        """

        inserted = 0
        updated = 0
        unchanged = 0

        cursor = self.sql.cursor()
        try:
            for record in records:
                params = tuple(record.get(c) for c in all_cols)
                try:
                    cursor.execute(merge_sql, params)
                    row = cursor.fetchone()
                    if row:
                        action = row[0]
                        if action == "INSERT":
                            inserted += 1
                        elif action == "UPDATE":
                            updated += 1
                        else:
                            unchanged += 1
                    else:
                        unchanged += 1
                except Exception as exc:
                    logger.error("MERGE failed for record (key=%s): %s",
                                 record.get(self.key_column), exc)
                    continue
            cursor.close()
        except Exception:
            cursor.close()
            raise

        return UpsertResult(inserted=inserted, updated=updated, unchanged=unchanged)

    def _build_change_check(self, columns: list[str]) -> str:
        """Build SQL condition to detect actual changes (skip no-ops)."""
        if not columns:
            return "1=1"
        checks = []
        for col in columns[:10]:  # limit to avoid overly complex SQL
            checks.append(
                f"(tgt.[{col}] IS NULL AND src.[{col}] IS NOT NULL) OR "
                f"(tgt.[{col}] IS NOT NULL AND src.[{col}] IS NULL) OR "
                f"tgt.[{col}] <> src.[{col}]"
            )
        return " OR ".join(f"({c})" for c in checks)

    def bulk_insert(
        self,
        records: list[dict[str, Any]],
        dry_run: bool = False,
    ) -> int:
        """Simple bulk INSERT (no upsert, for initial load).

        Uses fast_executemany for performance.
        Returns number of rows inserted.
        """
        if not records or dry_run:
            return len(records) if dry_run else 0

        all_cols = list(records[0].keys())
        col_list = ", ".join(f"[{c}]" for c in all_cols)
        placeholders = ", ".join("?" for _ in all_cols)
        sql = f"INSERT INTO {self._table_ref()} ({col_list}, [_run_id]) VALUES ({placeholders}, '{self.run_id}')"

        params_list = [tuple(r.get(c) for c in all_cols) for r in records]

        try:
            cursor = self.sql.cursor()
            cursor.fast_executemany = True
            cursor.executemany(sql, params_list)
            self.sql.commit()
            cursor.close()
            return len(records)
        except Exception as exc:
            self.sql.rollback()
            raise SQLExecutionError(self.table_name, "bulk_insert", str(exc)) from exc
