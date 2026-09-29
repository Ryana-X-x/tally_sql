"""TallyPrime ODBC client implementation.

Communicates with TallyPrime via its ODBC driver on Windows.

NOTE: The Tally ODBC driver is Windows-only. On Linux this module will
fail gracefully at connection time - this is expected in the development
environment. The module will function correctly when deployed to the
production Windows environment with Tally ODBC driver installed.

Setup on Windows:
    1. Enable ODBC server in TallyPrime: Gateway of Tally > F12 > ODBC
    2. ODBC port: 9000 (configurable)
    3. Configure DSN via Windows ODBC Data Source Administrator
       OR use DSN-less connection string
"""

from __future__ import annotations

import logging
from typing import Any, Generator, Optional

try:
    import pyodbc
    _HAS_PYODBC = True
except ImportError:
    pyodbc = None  # type: ignore
    _HAS_PYODBC = False

from tally_migrator.exceptions import (
    TallyConnectionError,
    TallyQueryError,
)
from tally_migrator.tally.base_client import BaseTallyClient
from tally_migrator.tally.xml_client import KNOWN_COLLECTIONS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tally ODBC SQL queries
# Tally ODBC exposes tables that correspond to Tally collections.
# The SQL dialect is a simplified subset.
# ---------------------------------------------------------------------------

COLLECTION_TO_TABLE: dict[str, str] = {
    "Ledger": "Ledger",
    "Group": "Group",
    "StockItem": "Stock Item",
    "StockGroup": "Stock Group",
    "VoucherType": "Voucher Type",
    "Currency": "Currency",
    "CostCentre": "Cost Centre",
    "CostCategory": "Cost Category",
    "Godown": "Godown",
    "Unit": "Unit",
    "Voucher": "Voucher",
}


class ODBCTallyClient(BaseTallyClient):
    """Tally ODBC client (Windows only).

    Uses pyodbc to query TallyPrime via its ODBC driver.

    The ODBC driver exposes Tally collections as SQL-queryable tables.
    This provides a familiar interface but is limited compared to TDL/XML.

    Connection string examples:
        DSN-based:    DRIVER={Tally ODBC Driver};Server=localhost;Port=9000
        DSN-less:     DRIVER={Tally ODBC Driver};Host=localhost;Port=9000

    NOTE: Windows-only. The application degrades gracefully on Linux.
    """

    def __init__(self, config):
        super().__init__(config)
        self._conn: Optional[Any] = None

    def _build_connection_string(self) -> str:
        if self.config.odbc_dsn:
            return f"DSN={self.config.odbc_dsn}"
        return (
            f"DRIVER={{{self.config.odbc_driver}}};"
            f"Host={self.config.host};"
            f"Port={self.config.port}"
        )

    def connect(self) -> None:
        if not _HAS_PYODBC:
            raise TallyConnectionError(
                self.config.host, self.config.port,
                "pyodbc not installed. Run: pip install pyodbc"
            )
        conn_str = self._build_connection_string()
        logger.info("Connecting via Tally ODBC: %s", conn_str)
        try:
            self._conn = pyodbc.connect(conn_str, timeout=self.config.timeout_seconds)
            self._conn.timeout = self.config.timeout_seconds
            logger.info("Tally ODBC connection established.")
        except pyodbc.Error as exc:
            raise TallyConnectionError(
                self.config.host, self.config.port,
                f"ODBC error: {exc}"
            ) from exc

    def close(self) -> None:
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def test_connection(self) -> dict[str, Any]:
        if not _HAS_PYODBC:
            return {"status": "unavailable", "reason": "pyodbc not installed"}
        try:
            if self._conn is None:
                self.connect()
            cursor = self._conn.cursor()
            cursor.execute("SELECT * FROM Company")
            companies = [row[0] for row in cursor.fetchall()]
            cursor.close()
            return {"status": "ok", "companies": companies}
        except Exception as exc:
            raise TallyConnectionError(self.config.host, self.config.port, str(exc)) from exc

    def list_companies(self) -> list[str]:
        cursor = self._conn.cursor()
        cursor.execute("SELECT Name FROM Company")
        result = [row[0] for row in cursor.fetchall()]
        cursor.close()
        return result

    def get_company_info(self, company: Optional[str] = None) -> dict[str, Any]:
        cursor = self._conn.cursor()
        if company:
            cursor.execute("SELECT * FROM Company WHERE Name = ?", company)
        else:
            cursor.execute("SELECT * FROM Company")
        row = cursor.fetchone()
        if row is None:
            return {}
        cols = [d[0] for d in cursor.description]
        cursor.close()
        return dict(zip(cols, row))

    def list_collections(self) -> list[str]:
        """List available Tally ODBC tables."""
        if not self._conn:
            return list(KNOWN_COLLECTIONS.keys())
        try:
            cursor = self._conn.cursor()
            tables = [row.table_name for row in cursor.tables(tableType="TABLE")]
            cursor.close()
            return tables if tables else list(KNOWN_COLLECTIONS.keys())
        except Exception:
            return list(KNOWN_COLLECTIONS.keys())

    def describe_collection(self, collection_name: str) -> dict[str, Any]:
        table_name = COLLECTION_TO_TABLE.get(collection_name, collection_name)
        known = KNOWN_COLLECTIONS.get(collection_name, {})
        fields = []
        if self._conn:
            try:
                cursor = self._conn.cursor()
                for col in cursor.columns(table=table_name):
                    fields.append({
                        "name": col.column_name,
                        "type": col.type_name,
                        "size": col.column_size,
                    })
                cursor.close()
            except Exception:
                pass
        if not fields:
            fields = [{"name": f, "type": "text"} for f in known.get("fields", [])]
        return {
            "name": collection_name,
            "table": table_name,
            "category": known.get("category", "unknown"),
            "fields": fields,
            "key_field": known.get("key_field"),
            "alteration_field": known.get("alteration_field"),
        }

    def count_records(self, collection_name: str, company: Optional[str] = None) -> int:
        table_name = COLLECTION_TO_TABLE.get(collection_name, collection_name)
        try:
            cursor = self._conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM [{table_name}]")
            result = cursor.fetchone()
            cursor.close()
            return result[0] if result else 0
        except Exception:
            return 0

    def fetch_all(
        self,
        collection_name: str,
        fields: Optional[list[str]] = None,
        filters: Optional[dict[str, Any]] = None,
        company: Optional[str] = None,
    ) -> Generator[dict[str, Any], None, None]:
        table_name = COLLECTION_TO_TABLE.get(collection_name, collection_name)
        field_list = ", ".join(f"[{f}]" for f in fields) if fields else "*"
        sql = f"SELECT {field_list} FROM [{table_name}]"
        params = []
        if company:
            sql += " WHERE Company = ?"
            params.append(company)
        try:
            cursor = self._conn.cursor()
            cursor.execute(sql, params)
            cols = [d[0] for d in cursor.description]
            while True:
                row = cursor.fetchone()
                if row is None:
                    break
                yield dict(zip(cols, row))
            cursor.close()
        except Exception as exc:
            raise TallyQueryError(collection_name, str(exc)) from exc

    def fetch_since(
        self,
        collection_name: str,
        since_marker: Optional[str],
        fields: Optional[list[str]] = None,
        company: Optional[str] = None,
    ) -> Generator[dict[str, Any], None, None]:
        if since_marker is None:
            yield from self.fetch_all(collection_name, fields=fields, company=company)
            return

        table_name = COLLECTION_TO_TABLE.get(collection_name, collection_name)
        known = KNOWN_COLLECTIONS.get(collection_name, {})
        alteration_field = known.get("alteration_field", "ALTERID")
        field_list = ", ".join(f"[{f}]" for f in fields) if fields else "*"

        try:
            marker_int = int(since_marker)
            sql = f"SELECT {field_list} FROM [{table_name}] WHERE [{alteration_field}] > ?"
            params = [marker_int]
        except (ValueError, TypeError):
            sql = f"SELECT {field_list} FROM [{table_name}]"
            params = []

        try:
            cursor = self._conn.cursor()
            cursor.execute(sql, params)
            cols = [d[0] for d in cursor.description]
            while True:
                row = cursor.fetchone()
                if row is None:
                    break
                yield dict(zip(cols, row))
            cursor.close()
        except Exception as exc:
            raise TallyQueryError(collection_name, str(exc)) from exc
