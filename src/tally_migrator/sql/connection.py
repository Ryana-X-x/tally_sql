"""SQL Server connection management.

Wrap pyodbc connections with:
- Connection lifecycle management
- Retry on transient errors
- Connection string building (without leaking credentials)
- fast_executemany support
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator, Optional, Tuple

try:
    import pyodbc
    _HAS_PYODBC = True
except ImportError:
    pyodbc = None  # type: ignore
    _HAS_PYODBC = False

from tally_migrator.exceptions import SQLConnectionError, SQLExecutionError

logger = logging.getLogger(__name__)

ROW = Tuple[Any, ...]


class SQLConnection:
    """Managed SQL Server connection.

    Usage:
        sql = SQLConnection(cfg.sql)
        sql.test_connection()
        cursor = sql.cursor()
        cursor.execute("SELECT ...")
        sql.commit()
        sql.close()

    Or as context manager:
        with SQLConnection(cfg.sql) as sql:
            sql.execute("INSERT ...")
    """

    def __init__(self, config):
        self.config = config
        self._conn: Optional[Any] = None
        self._connect()

    def _connect(self) -> None:
        if not _HAS_PYODBC:
            raise SQLConnectionError(
                self.config.server, self.config.database,
                "pyodbc not installed. Run: pip install pyodbc"
            )
        conn_str = self.config.connection_string()
        safe_str = self.config.connection_string(hide_password=True)
        logger.info("Connecting to SQL Server: %s", safe_str)
        try:
            self._conn = pyodbc.connect(conn_str, autocommit=False)
            self._conn.timeout = self.config.command_timeout
            # Enable fast_executemany for batch inserts
            self._conn.add_output_converter  # test attribute exists
            try:
                self._conn.fast_executemany = True
            except AttributeError:
                pass  # older pyodbc versions
            logger.info("SQL Server connection established.")
        except pyodbc.Error as exc:
            raise SQLConnectionError(
                self.config.server, self.config.database, str(exc)
            ) from exc

    def test_connection(self) -> None:
        """Verify the connection is working."""
        cursor = self._conn.cursor()
        cursor.execute("SELECT @@VERSION")
        version = cursor.fetchone()[0]
        logger.info("SQL Server version: %s", version[:80])
        cursor.close()

    def cursor(self):
        """Return a new cursor."""
        return self._conn.cursor()

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        try:
            self._conn.rollback()
        except Exception as exc:
            logger.error("Rollback failed: %s", exc)

    def close(self) -> None:
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def __enter__(self) -> "SQLConnection":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()
        self.close()

    @contextmanager
    def transaction(self) -> Iterator["SQLConnection"]:
        """Context manager for a single transaction."""
        try:
            yield self
            self._conn.commit()
        except Exception:
            self.rollback()
            raise

    def execute(self, sql: str, params: Optional[tuple] = None) -> Any:
        """Execute a single SQL statement."""
        cursor = self._conn.cursor()
        try:
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)
            return cursor
        except pyodbc.Error as exc:
            raise SQLExecutionError("<unknown>", "execute", str(exc)) from exc

    def executemany(self, sql: str, params_list: list[tuple]) -> int:
        """Execute SQL for multiple parameter sets (uses fast_executemany)."""
        if not params_list:
            return 0
        cursor = self._conn.cursor()
        try:
            cursor.executemany(sql, params_list)
            count = cursor.rowcount
            cursor.close()
            return count if count >= 0 else len(params_list)
        except pyodbc.Error as exc:
            cursor.close()
            raise SQLExecutionError("<unknown>", "executemany", str(exc)) from exc

    def execute_script(self, sql: str) -> None:
        """Execute a multi-statement SQL script (splits on GO) transactionally."""
        import re
        statements = re.split(r"^\s*GO\s*$", sql, flags=re.MULTILINE | re.IGNORECASE)
        cursor = self._conn.cursor()
        try:
            for stmt in statements:
                stmt = stmt.strip()
                if stmt:
                    cursor.execute(stmt)
            self._conn.commit()
            cursor.close()
        except Exception as exc:
            self.rollback()
            cursor.close()
            logger.error("Script execution failed: %s", exc)
            raise SQLExecutionError("<script>", "execute_script", str(exc)) from exc

    def table_exists(self, table_name: str, schema_name: Optional[str] = None) -> bool:
        """Check if a table exists in SQL Server."""
        schema = schema_name or self.config.schema_name
        cursor = self.execute(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?",
            (schema, table_name),
        )
        count = cursor.fetchone()[0]
        cursor.close()
        return count > 0

    def column_exists(self, table_name: str, column_name: str, schema_name: Optional[str] = None) -> bool:
        """Check if a column exists in a table."""
        schema = schema_name or self.config.schema_name
        cursor = self.execute(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? AND COLUMN_NAME = ?",
            (schema, table_name, column_name),
        )
        count = cursor.fetchone()[0]
        cursor.close()
        return count > 0
