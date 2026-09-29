"""Schema validation: compare discovered Tally schema vs actual SQL Server schema."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from tally_migrator.schema.discovery import SchemaDiscovery

logger = logging.getLogger(__name__)


@dataclass
class ValidationIssue:
    severity: str     # 'error', 'warning', 'info'
    category: str     # 'missing_table', 'missing_column', 'type_mismatch', etc.
    message: str
    table: Optional[str] = None
    column: Optional[str] = None


@dataclass
class ValidationResult:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def print_report(self) -> None:
        print()
        print("=" * 60)
        print("SCHEMA VALIDATION REPORT")
        print("=" * 60)
        if not self.issues:
            print("  No issues found. Schema is in sync.")
            return

        for issue in self.issues:
            prefix = {"error": "[ERROR]", "warning": "[WARN] ", "info": "[INFO] "}.get(issue.severity, "[ ? ]  ")
            loc = f" ({issue.table}" + (f".{issue.column}" if issue.column else "") + ")" if issue.table else ""
            print(f"  {prefix} {issue.message}{loc}")

        print()
        print(f"  Errors:   {len(self.errors)}")
        print(f"  Warnings: {len(self.warnings)}")
        print(f"  Status:   {'PASS' if self.is_valid else 'FAIL'}")
        print()


class SchemaValidator:
    """Compares discovered Tally schema against actual SQL Server tables."""

    def __init__(self, schema_path: Path, sql_connection, config):
        self.schema_path = schema_path
        self.sql = sql_connection
        self.config = config

    def validate(self) -> ValidationResult:
        result = ValidationResult()

        # Load discovered schema
        try:
            schema = SchemaDiscovery.load(self.schema_path)
        except Exception as exc:
            result.issues.append(ValidationIssue(
                severity="error",
                category="load_error",
                message=f"Cannot load discovered schema: {exc}",
            ))
            return result

        # Get existing SQL tables
        sql_tables = self._get_sql_tables()
        sql_columns: dict[str, dict[str, str]] = {}
        for tbl in sql_tables:
            sql_columns[tbl] = self._get_sql_columns(tbl)

        schema_name = self.config.sql.schema_name

        # Check each collection
        for coll_name, coll in schema.collections.items():
            table_name = coll.sql_table_name or coll_name.lower()
            full_table = f"{schema_name}.{table_name}"

            if table_name not in sql_tables and full_table not in sql_tables:
                result.issues.append(ValidationIssue(
                    severity="error",
                    category="missing_table",
                    message=f"SQL table does not exist for collection '{coll_name}'",
                    table=table_name,
                ))
                continue

            # Check columns
            actual_cols = sql_columns.get(table_name, sql_columns.get(full_table, {}))
            for field_name, field_def in coll.fields.items():
                col_name = field_name.lower()
                if col_name not in {k.lower() for k in actual_cols}:
                    result.issues.append(ValidationIssue(
                        severity="warning",
                        category="missing_column",
                        message=f"Column '{col_name}' not found in SQL table",
                        table=table_name,
                        column=col_name,
                    ))

            # Check for unmapped fields
            for uf in coll.unmapped_fields:
                result.issues.append(ValidationIssue(
                    severity="warning",
                    category="unmapped_field",
                    message=f"Field '{uf}' discovered but not mapped",
                    table=table_name,
                    column=uf,
                ))

        logger.info("Validation complete: %d issues", len(result.issues))
        return result

    def _get_sql_tables(self) -> list[str]:
        try:
            cursor = self.sql.cursor()
            cursor.execute(
                "SELECT TABLE_SCHEMA + '.' + TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_TYPE = 'BASE TABLE'"
            )
            return [row[0] for row in cursor.fetchall()]
        except Exception as exc:
            logger.error("Cannot query SQL tables: %s", exc)
            return []

    def _get_sql_columns(self, table_name: str) -> dict[str, str]:
        try:
            parts = table_name.split(".")
            if len(parts) == 2:
                schema, table = parts
            else:
                schema, table = "dbo", parts[0]
            cursor = self.sql.cursor()
            cursor.execute(
                "SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?",
                (schema, table)
            )
            return {row[0]: row[1] for row in cursor.fetchall()}
        except Exception:
            return {}
